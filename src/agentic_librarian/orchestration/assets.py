import mlflow
import pandas as pd
from agentic_librarian.db.models import Author, Edition, Work
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.ingest import HistoryIngestor
from agentic_librarian.scouts.metadata_scout import MultiSourceScout
from agentic_librarian.scouts.trope_manager import TropeManager
from dagster import AssetExecutionContext, DynamicPartitionsDefinition, MetadataValue, ResourceParam, asset

csv_partitions = DynamicPartitionsDefinition(name="csv_files")


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
def enriched_metadata(context: AssetExecutionContext, raw_history: pd.DataFrame) -> pd.DataFrame:
    """Enriches reading history with metadata from multiple scouts."""
    scout = MultiSourceScout()
    enriched_rows = []

    mlflow.set_experiment("metadata_enrichment")
    with mlflow.start_run(run_name=f"enrich_{context.run.run_id}"):
        for _, row in raw_history.iterrows():
            # Handle split authors: primary author is in Author_1
            primary_author = row.get("Author_1") or row.get("Author")

            metadata = scout.scout_metadata(title=row["Title"], author=primary_author, format=row["format"])
            # Merge original row with metadata
            combined = {**row.to_dict(), **metadata}
            enriched_rows.append(combined)

    enriched_df = pd.DataFrame(enriched_rows)
    context.add_output_metadata({"enriched_count": len(enriched_df), "columns": list(enriched_df.columns)})
    return enriched_df


@asset(partitions_def=csv_partitions)
def vectorized_tropes(
    context: AssetExecutionContext, enriched_metadata: pd.DataFrame, db_manager: ResourceParam[DatabaseManager]
) -> None:
    """Standardizes tropes and saves metadata to the database."""
    with db_manager.get_session() as session:
        trope_manager = TropeManager(session=session)

        for _, row in enriched_metadata.iterrows():
            # 1. Author & Work (using simple mapping for now, ideally would use ingestor.to_models)
            # But we want to update with enriched facts

            # This is a bit simplified - in a real app we'd have more robust merging logic
            author_names = row["authors"]
            authors = []
            for name in author_names:
                author = session.query(Author).filter(Author.name == name).first()
                if not author:
                    author = Author(name=name)
                    session.add(author)
                authors.append(author)

            work = session.query(Work).filter(Work.title == row["title"]).first()
            if not work:
                work = Work(
                    title=row["title"],
                    authors=authors,
                    original_publication_year=row.get("original_publication_year"),
                    description=row.get("description"),
                    genres=row.get("genres"),
                    moods=row.get("moods"),
                )
                session.add(work)

            edition = session.query(Edition).filter(Edition.isbn_13 == row.get("isbn_13")).first()
            if not edition:
                edition = Edition(
                    work=work,
                    isbn_13=row.get("isbn_13"),
                    format=row.get("format"),
                    page_count=row.get("page_count"),
                    audio_minutes=row.get("audio_minutes"),
                    publication_date=row.get("publication_date"),
                )
                session.add(edition)

            # 2. Tropes
            raw_genres = row.get("genres", [])
            raw_moods = row.get("moods", [])
            all_tags = set(raw_genres + raw_moods)

            for tag in all_tags:
                standardized_trope = trope_manager.standardize_trope(tag)
                # Link work to trope
                # Check if link exists
                from agentic_librarian.db.models import WorkTrope

                existing_link = (
                    session.query(WorkTrope).filter_by(work_id=work.id, trope_id=standardized_trope.id).first()
                )

                if not existing_link:
                    work_trope = WorkTrope(work=work, trope=standardized_trope)
                    session.add(work_trope)

    context.log.info("Successfully vectorized tropes and updated database.")
