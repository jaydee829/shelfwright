import os
from uuid import UUID

import mlflow
import pandas as pd
from dagster import AssetExecutionContext, DynamicPartitionsDefinition, MetadataValue, ResourceParam, asset

from agentic_librarian.core.user_context import DEFAULT_USER_ID, as_user
from agentic_librarian.db.models import (
    Author,
    Edition,
    Work,
    WorkContributor,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.ingest import HistoryIngestor
from agentic_librarian.etl.persist import persist_enriched_work
from agentic_librarian.scouts.metadata_scout import ScoutManager
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager

csv_partitions = DynamicPartitionsDefinition(name="csv_files")


def _ingest_user_id() -> UUID:
    """Bulk imports are operator-run; INGEST_USER_ID targets a friend's account
    (DEBT-001: 'friends send the operator a CSV'). Defaults to the operator."""
    raw = os.environ.get("INGEST_USER_ID")
    return UUID(raw) if raw else DEFAULT_USER_ID


@asset(partitions_def=csv_partitions)
def raw_history(context: AssetExecutionContext) -> pd.DataFrame:
    """Loads and cleans raw reading history from CSV."""
    try:
        partition_key = context.partition_key
        df = pd.read_csv(f"data/raw/{partition_key}.csv")
    except FileNotFoundError:
        context.log.error(f"File data/raw/{context.partition_key}.csv not found.")
        raise

    ingestor = HistoryIngestor(df)
    cleaned_df = ingestor.clean()

    context.add_output_metadata(
        {"row_count": len(cleaned_df), "preview": MetadataValue.md(cleaned_df.head(5).to_markdown())}
    )
    return cleaned_df


@asset(partitions_def=csv_partitions)
def enriched_metadata(
    context: AssetExecutionContext,
    raw_history: pd.DataFrame,
    db_manager: ResourceParam[DatabaseManager],
    scout_manager: ResourceParam[ScoutManager],
) -> pd.DataFrame:
    """Enriches reading history using the ScoutManager resource, skipping existing ones."""
    enriched_rows = []

    mlflow.set_experiment("metadata_enrichment")
    with mlflow.start_run(run_name=f"enrich_{context.run.run_id}"), db_manager.get_session() as session:
        for _, row in raw_history.iterrows():
            # Handle split authors: primary author is in Author_1
            primary_author = row.get("Author_1") or row.get("Author")
            title = row["Title"]
            fmt = row["format"]

            # Check if we already have this Edition (Work + Format)
            # This minimizes API calls
            existing_edition = (
                session.query(Edition)
                .join(Work)
                .join(WorkContributor)
                .join(Author)
                .filter(Work.title == title)
                .filter(Author.name == primary_author)
                .filter(Edition.format == fmt)
                .first()
            )

            if existing_edition:
                context.log.info(f"Skipping enrichment for existing edition: {title} ({fmt})")
                # Still include the row for ReadingHistory processing later
                enriched_rows.append({**row.to_dict(), "skip_enrichment": True})
            else:
                context.log.info(f"Enriching new entry: {title} ({fmt})")

                # Informed Scouting: Fetch existing author baseline
                author_styles = {}
                author_obj = session.query(Author).filter(Author.name == primary_author).first()
                if author_obj:
                    author_styles = {s.attribute_type: s.style.name for s in author_obj.styles}

                metadata = scout_manager.enrich(
                    title=title, author=primary_author, format=fmt, author_styles=author_styles
                )
                # Merge original row with metadata
                combined = {**row.to_dict(), **metadata, "skip_enrichment": False}
                enriched_rows.append(combined)

    enriched_df = pd.DataFrame(enriched_rows)
    context.add_output_metadata({"enriched_count": len(enriched_df), "columns": list(enriched_df.columns)})
    return enriched_df


@asset(partitions_def=csv_partitions)
def vectorized_tropes(
    context: AssetExecutionContext, enriched_metadata: pd.DataFrame, db_manager: ResourceParam[DatabaseManager]
) -> None:
    """Standardizes tropes/styles and saves metadata + reading history to the database."""

    with as_user(_ingest_user_id()), db_manager.get_session() as session:
        trope_manager = TropeManager(session=session)
        style_manager = StyleManager(session=session)
        for _, row in enriched_metadata.iterrows():
            if persist_enriched_work(session, row.to_dict(), trope_manager, style_manager) is None:
                context.log.warning(f"No contributors found for '{row['Title']}'. Skipping work creation.")
    context.log.info("Successfully vectorized tropes and updated database.")
