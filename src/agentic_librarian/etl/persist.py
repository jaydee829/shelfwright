"""Shared persistence for an enriched book row. Used by the Flow-1 ETL asset
(`vectorized_tropes`) and the recommendation enrichment tool (`enrich_and_persist_work`),
so both paths build the catalog identically (DRY)."""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy.orm import Session

from agentic_librarian.core.user_context import get_required_user_id
from agentic_librarian.db.models import (
    Author,
    AuthorStyle,
    Edition,
    Narrator,
    NarratorStyle,
    ReadingHistory,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
)
from agentic_librarian.etl.contributor_dedup import norm_name
from agentic_librarian.etl.tag_cleaning import clean_genres, clean_moods
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager

logger = logging.getLogger(__name__)


def _safe_standardize(fn, *args, label: str, **kwargs):
    """Run a standardize_* embedding call, returning its Trope/Style, or None on failure.
    An embedding API error (bad/transient key, 429/5xx) must skip that one vectorization,
    not abort the whole persist (REC-021/REC-023 degrade-gracefully pattern). Takes the
    callable + its args directly (not a lambda) so loop variables bind by value (avoids B023)."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 - any embedding/standardize failure degrades to skip-this-item
        logger.warning("skipping vectorization for %s (embedding/standardize failed)", label, exc_info=True)
        return None


def _iter_style_items(style_data: dict | None, owner_label: str):
    """Yield (attr_type, style_name) for valid non-empty string values only. A malformed scout
    response can nest a value as a dict/list; passing that to standardize_style would make it a
    Style.name and raise 'can't adapt type dict'. Skip + warn instead so persistence degrades
    gracefully (REC-021)."""
    if not isinstance(style_data, dict):
        return
    for attr_type, style_name in style_data.items():
        if isinstance(style_name, str) and style_name.strip():
            yield attr_type, style_name.strip()
        elif style_name:
            print(f"Warning: skipping non-string style '{attr_type}'={type(style_name).__name__} for {owner_label}")


def _nan_to_none(value):
    """Coerce a pandas NaN/NaT scalar to None so it inserts as SQL NULL. Enrichment/CSV columns
    arrive via pandas, which fills missing scalars with NaN (a float); inserting that into a typed
    column (date/int) raises DatatypeMismatch. Real values — including strings — pass through, and a
    non-scalar (list/dict) is returned unchanged rather than raising on pd.isna."""
    try:
        return None if pd.isna(value) else value
    except (TypeError, ValueError):
        return value


def _nan_to_list(value):
    """Return value if it is a list, else [] — so an enrichment column that pandas filled with NaN
    (a float) or None doesn't crash downstream iteration/set() with 'float object is not iterable'."""
    return value if isinstance(value, list) else []


def persist_enriched_work(
    session: Session, row: dict, trope_manager: TropeManager, style_manager: StyleManager
) -> Work | None:
    """Create/Update the Work graph for one enriched row. Returns the Work, or None if the
    row has no contributors. Does NOT commit — the caller controls the transaction."""
    # 1. Contributors (Name + Role)
    # Use enriched contributors if available, otherwise fallback to CSV authors
    raw_contributors = row.get("contributors")
    if not isinstance(raw_contributors, list):
        # Fallback: Extract from Author_X columns
        author_cols = [c for c in row if c.startswith("Author_")]
        raw_contributors = []
        for col in author_cols:
            name = row[col]
            if name and not pd.isna(name):
                raw_contributors.append({"name": name, "role": "Author"})

    # Drop contributors without a usable name: malformed scout output can yield {"name": None} (or
    # blank/NaN), which violates the authors.name NOT NULL constraint on insert.
    raw_contributors = [
        c for c in raw_contributors if isinstance(c, dict) and isinstance(c.get("name"), str) and c["name"].strip()
    ]

    if not raw_contributors:
        return None

    # Resolve/Create Authors and create WorkContributor objects
    work_contributors_list = []
    author_style_data = row.get("author_style", {})

    seen_contributors: set[tuple[str, str]] = set()
    for c_data in raw_contributors:
        name = c_data["name"].strip()
        # A whitespace-only role is truthy and a non-string role would persist as-is; both
        # must fall back to "Author" (PR #30 review). Valid roles keep their stripped value.
        role = c_data.get("role")
        role = role.strip() if isinstance(role, str) and role.strip() else "Author"
        key = (norm_name(name), role)
        if key in seen_contributors:  # guard: never write the same author+role twice
            continue
        seen_contributors.add(key)
        author = session.query(Author).filter(Author.name == name).first()
        if not author:
            author = Author(name=name)
            session.add(author)
            session.flush()

        # Process Author Styles if role is Author
        if role == "Author" and author_style_data:
            for attr_type, style_name in _iter_style_items(author_style_data, f"Author '{name}'"):
                standard_style = _safe_standardize(
                    style_manager.standardize_style, style_name, category="Author", label=f"style {style_name!r}"
                )
                if standard_style is None:
                    continue
                existing_link = (
                    session.query(AuthorStyle)
                    .filter_by(author_id=author.id, style_id=standard_style.id, attribute_type=attr_type)
                    .first()
                )
                if not existing_link:
                    session.add(AuthorStyle(author=author, style=standard_style, attribute_type=attr_type))

        work_contributors_list.append(WorkContributor(author=author, role=role))

    # Coerce every enrichment/CSV field that pandas may deliver as NaN before it reaches the ORM.
    # Scalars -> None (SQL NULL); list-typed fields -> [] so downstream iteration/set() can't crash
    # on a float NaN. This single pass is the guard against both 'float object is not iterable' and
    # DatatypeMismatch (NaN into an Integer/Date column) across the whole persist path.
    original_publication_year = _nan_to_none(row.get("original_publication_year"))
    description = _nan_to_none(row.get("description"))
    genres = clean_genres(_nan_to_list(row.get("genres")))
    moods = clean_moods(_nan_to_list(row.get("moods")))
    enriched_tropes = _nan_to_list(row.get("enriched_tropes"))
    user_rating = _nan_to_none(row.get("user_rating"))
    user_notes = _nan_to_none(row.get("user_notes"))
    isbn_13 = _nan_to_none(row.get("isbn_13"))
    page_count = _nan_to_none(row.get("page_count"))
    audio_minutes = _nan_to_none(row.get("audio_minutes"))
    publication_date = _nan_to_none(row.get("publication_date"))

    # 2. Work. no_autoflush: the not-yet-added WorkContributor objects above must not be
    # cascaded by this query's autoflush (raises a SAWarning and could flush incomplete rows).
    with session.no_autoflush:
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
            original_publication_year=original_publication_year,
            description=description,
            genres=genres,
            moods=moods,
        )
        session.add(work)
        session.flush()
    elif not row.get("skip_enrichment"):
        # Update existing work if new metadata found
        work.original_publication_year = original_publication_year or work.original_publication_year
        work.description = description or work.description
        work.genres = genres or work.genres
        work.moods = moods or work.moods

    # 2.5 Work Styles
    work_style_data = row.get("work_style", {})
    if work_style_data:
        for attr_type, style_name in _iter_style_items(work_style_data, f"Work '{row.get('Title')}'"):
            standard_style = _safe_standardize(
                style_manager.standardize_style, style_name, category="Work", label=f"style {style_name!r}"
            )
            if standard_style is None:
                continue
            existing_link = (
                session.query(WorkStyle)
                .filter_by(work_id=work.id, style_id=standard_style.id, attribute_type=attr_type)
                .first()
            )
            if not existing_link:
                session.add(WorkStyle(work=work, style=standard_style, attribute_type=attr_type))

    # 3. Edition & Narrators
    edition = session.query(Edition).filter_by(work_id=work.id, format=row["format"]).first()

    # Resolve Narrators. A row may carry narrator_names/styles as NaN (float) — pandas fills the
    # column with NaN for rows that lack it (e.g. skip_enrichment rows mixed with audiobook rows in
    # the same partition DataFrame). Coerce non-list/dict to empty so persist never crashes on it.
    narrator_objs = []
    narrator_names = row.get("narrator_names")
    if not isinstance(narrator_names, list):
        narrator_names = []
    narrator_styles = row.get("narrator_styles")
    if not isinstance(narrator_styles, dict):
        narrator_styles = {}

    seen_narr: set[str] = set()
    deduped_names = []
    for n_name in narrator_names:
        k = norm_name(n_name)
        if k not in seen_narr:
            seen_narr.add(k)
            deduped_names.append(n_name)
    narrator_names = deduped_names
    for n_name in narrator_names:
        narrator = session.query(Narrator).filter(Narrator.name == n_name).first()
        if not narrator:
            narrator = Narrator(name=n_name)
            session.add(narrator)
            session.flush()

        # Process Narrator Styles
        n_style_data = narrator_styles.get(n_name, {})
        if n_style_data:
            for attr_type, style_name in _iter_style_items(n_style_data, f"Narrator '{n_name}'"):
                standard_style = _safe_standardize(
                    style_manager.standardize_style, style_name, category="Narrator", label=f"style {style_name!r}"
                )
                if standard_style is None:
                    continue
                existing_link = (
                    session.query(NarratorStyle)
                    .filter_by(narrator_id=narrator.id, style_id=standard_style.id, attribute_type=attr_type)
                    .first()
                )
                if not existing_link:
                    session.add(NarratorStyle(narrator=narrator, style=standard_style, attribute_type=attr_type))

        narrator_objs.append(narrator)

    if not edition:
        edition = Edition(
            work=work,
            isbn_13=isbn_13,
            format=row.get("format"),
            page_count=page_count,
            audio_minutes=audio_minutes,
            publication_date=publication_date,
            narrators=narrator_objs,
        )
        session.add(edition)
        session.flush()  # Ensure edition.id is populated for ReadingHistory check
    else:
        if not row.get("skip_enrichment"):
            # Update existing edition if new metadata found
            edition.isbn_13 = isbn_13 or edition.isbn_13
            edition.page_count = page_count or edition.page_count
            edition.audio_minutes = audio_minutes or edition.audio_minutes

        # Update narrators if needed
        if narrator_objs:
            edition.narrators = list(set(edition.narrators) | set(narrator_objs))

    # 4. Reading History (The actual read event). pd.NaT/NaN are truthy, so guard with pd.isna
    # before calling .date() (which would raise on NaT).
    _raw_date = row.get("date_completed")
    date_completed = pd.to_datetime(_raw_date).date() if _raw_date is not None and not pd.isna(_raw_date) else None

    if date_completed:
        user_id = get_required_user_id()  # per-user: a friend re-reading my book is not a duplicate (ADR-048)
        existing_history = (
            session.query(ReadingHistory)
            .filter_by(edition_id=edition.id, date_completed=date_completed, user_id=user_id)
            .first()
        )

        if not existing_history:
            history_entry = ReadingHistory(
                edition=edition,
                user_id=user_id,
                date_completed=date_completed,
                user_rating=user_rating,
                user_notes=user_notes,
            )
            session.add(history_entry)

    # 5. Tropes (Only if enriched). enriched_tropes/genres/moods were NaN-coerced to lists above,
    # so the truthy check and set() iteration below are safe even when pandas filled them with NaN.
    if not row.get("skip_enrichment"):
        # Handle Fallback Tags (Moods/Genres)
        all_fallback_tags = set(genres) | set(moods)

        if enriched_tropes:
            for t_data in enriched_tropes:
                name = t_data["trope_name"]
                desc = t_data.get("description")
                score = t_data.get("relevance_score", 1.0)
                just = t_data.get("justification")

                standardized_trope = _safe_standardize(
                    trope_manager.standardize_trope, name, description=desc, label=f"trope {name!r}"
                )
                if standardized_trope is None:
                    continue
                existing_link = (
                    session.query(WorkTrope).filter_by(work_id=work.id, trope_id=standardized_trope.id).first()
                )
                if not existing_link:
                    session.add(
                        WorkTrope(work=work, trope=standardized_trope, relevance_score=score, justification=just)
                    )
                else:
                    # Update score/justification if they were missing
                    existing_link.relevance_score = score
                    existing_link.justification = existing_link.justification or just
        else:
            # Fallback to simple tags if no enriched tropes found
            for tag in all_fallback_tags:
                standardized_trope = _safe_standardize(trope_manager.standardize_trope, tag, label=f"trope {tag!r}")
                if standardized_trope is None:
                    continue
                existing_link = (
                    session.query(WorkTrope).filter_by(work_id=work.id, trope_id=standardized_trope.id).first()
                )
                if not existing_link:
                    work_trope = WorkTrope(work=work, trope=standardized_trope)
                    session.add(work_trope)

    return work
