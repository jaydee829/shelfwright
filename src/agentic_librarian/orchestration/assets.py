import mlflow
import pandas as pd
from agentic_librarian.db.models import Author, Edition, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.ingest import HistoryIngestor
from agentic_librarian.scouts.metadata_scout import ScoutManager
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
                metadata = scout_manager.enrich(title=title, author=primary_author, format=fmt)
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
    """Standardizes tropes and saves metadata + reading history to the database."""
    from agentic_librarian.db.models import ReadingHistory, WorkTrope

    with db_manager.get_session() as session:
        trope_manager = TropeManager(session=session)

        for _, row in enriched_metadata.iterrows():
            # 1. Contributors (Name + Role)
            # Use enriched contributors if available, otherwise fallback to CSV authors
            raw_contributors = row.get("contributors")
            if not isinstance(raw_contributors, list):
                # Fallback: Extract from Author_X columns
                author_cols = [c for c in enriched_metadata.columns if c.startswith("Author_")]
                raw_contributors = []
                for col in author_cols:
                    name = row[col]
                    if name and not pd.isna(name):
                        raw_contributors.append({"name": name, "role": "Author"})

            if not raw_contributors:
                context.log.warning(f"No contributors found for '{row['Title']}'. Skipping work creation.")
                continue

            # Resolve/Create Authors and create WorkContributor objects
            work_contributors_list = []
            for c_data in raw_contributors:
                name = c_data["name"]
                role = c_data["role"]
                author = session.query(Author).filter(Author.name == name).first()
                if not author:
                    author = Author(name=name)
                    session.add(author)
                    session.flush()

                work_contributors_list.append(WorkContributor(author=author, role=role))

            # 2. Work
            work = (
                session.query(Work)
                .join(WorkContributor)
                .join(Author)
                .filter(Work.title == row["Title"])
                .filter(Author.name == (row.get("Author_1") or row.get("Author")))
                .first()
            )
            if not work:
                work = Work(
                    title=row["Title"],
                    contributors=work_contributors_list,
                    original_publication_year=row.get("original_publication_year"),
                    description=row.get("description"),
                    genres=row.get("genres"),
                    moods=row.get("moods"),
                )
                session.add(work)
                session.flush()
            elif not row.get("skip_enrichment"):
                # Update existing work if new metadata found
                work.original_publication_year = row.get("original_publication_year") or work.original_publication_year
                work.description = row.get("description") or work.description
                work.genres = row.get("genres") or work.genres
                work.moods = row.get("moods") or work.moods

            # 3. Edition
            edition = session.query(Edition).filter_by(work_id=work.id, format=row["format"]).first()
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
                session.flush()  # Ensure edition.id is populated for ReadingHistory check
            elif not row.get("skip_enrichment"):
                # Update existing edition if new metadata found
                edition.isbn_13 = row.get("isbn_13") or edition.isbn_13
                edition.page_count = row.get("page_count") or edition.page_count
                edition.audio_minutes = row.get("audio_minutes") or edition.audio_minutes

            # 4. Reading History (The actual read event)
            date_completed = pd.to_datetime(row["date_completed"]).date() if row.get("date_completed") else None

            if date_completed:
                # Duplicate Check: Work + Edition + Date Completed
                existing_history = (
                    session.query(ReadingHistory)
                    .filter_by(edition_id=edition.id, date_completed=date_completed)
                    .first()
                )

                if not existing_history:
                    history_entry = ReadingHistory(
                        edition=edition,
                        date_completed=date_completed,
                        user_rating=row.get("user_rating"),
                        user_notes=row.get("user_notes"),
                    )
                    session.add(history_entry)
                    context.log.info(f"Added reading history for {row['Title']} on {date_completed}")
                else:
                    context.log.info(f"Reading history already exists for {row['Title']} on {date_completed}")

            # 2. Tropes (Only if enriched)
            if not row.get("skip_enrichment"):
                raw_genres = row.get("genres")
                if not isinstance(raw_genres, list | set):
                    raw_genres = []

                raw_moods = row.get("moods")
                if not isinstance(raw_moods, list | set):
                    raw_moods = []

                all_tags = set(raw_genres) | set(raw_moods)

                for tag in all_tags:
                    standardized_trope = trope_manager.standardize_trope(tag)
                    existing_link = (
                        session.query(WorkTrope).filter_by(work_id=work.id, trope_id=standardized_trope.id).first()
                    )
                    if not existing_link:
                        work_trope = WorkTrope(work=work, trope=standardized_trope)
                        session.add(work_trope)

    context.log.info("Successfully vectorized tropes and updated database.")
