"""Shared persistence for an enriched book row. Used by the Flow-1 ETL asset
(`vectorized_tropes`) and the recommendation enrichment tool (`enrich_and_persist_work`),
so both paths build the catalog identically (DRY)."""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from agentic_librarian.core.user_context import get_required_user_id
from agentic_librarian.db.get_or_create import get_or_create, insert_or_requery
from agentic_librarian.db.models import (
    Author,
    AuthorStyle,
    Edition,
    Narrator,
    NarratorStyle,
    ReadingHistory,
    Trope,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
)
from agentic_librarian.etl.contributor_dedup import norm_name
from agentic_librarian.etl.tag_cleaning import clean_genres, clean_moods, clean_trope_name
from agentic_librarian.etl.trope_predicate import is_fallback_trope_name
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager

logger = logging.getLogger(__name__)


def merge_edition_and_narrators(
    session,
    *,
    work_id,
    fmt,
    isbn_13=None,
    page_count=None,
    audio_minutes=None,
    publication_date=None,
    narrator_names=None,
    narrator_styles=None,
    style_manager,
    apply_metadata=True,
):
    """Resolve narrators (+ narrator styles) and get-or-create/merge the (work_id, fmt)
    Edition. Extracted from persist_enriched_work (history-format-edit spec) so the
    format-completion pass (two_phase.complete_edition) shares the exact same merge
    semantics. apply_metadata=False (persist's skip_enrichment rows) still merges
    narrators, mirroring the original gating. Returns the Edition, flushed when newly
    created so its id is populated for the caller."""
    edition = session.query(Edition).filter_by(work_id=work_id, format=fmt).first()

    # A row may carry narrator_names/styles as NaN (float) — pandas fills the column with
    # NaN for rows that lack it. Coerce non-list/dict to empty so this never crashes.
    if not isinstance(narrator_names, list):
        narrator_names = []
    # Keep only non-empty strings: a malformed/NaN element would crash norm_name/.lower().
    narrator_names = [n for n in narrator_names if isinstance(n, str) and n.strip()]
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

    narrator_objs = []
    for n_name in narrator_names:
        # Case-insensitive lookup so a casing variant reuses the existing Narrator row.
        narrator = session.query(Narrator).filter(func.lower(Narrator.name) == n_name.lower()).first()
        if not narrator:
            # GH #95: uq_narrators_name_lower — same case-insensitive requery pattern as Author.
            narrator, _created = insert_or_requery(
                session,
                Narrator(name=n_name),
                lambda n_name=n_name: (
                    session.query(Narrator).filter(func.lower(Narrator.name) == n_name.lower()).first()
                ),
            )

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
        # GH #95: uq_editions_work_format backstops the SELECT-then-INSERT race above; a
        # concurrent persist for the same (work_id, format) recovers via requery instead of
        # a 500. narrators/other creation-only fields only apply on the winning insert — the
        # loser's edition is re-queried as-is and updated by the existing-edition branch on
        # its NEXT call (mirrors the pre-#95 eventual-consistency behavior).
        # work_id= (not work=) so the not-yet-added Edition never lands in work.editions via
        # the back_populates backref before session.add — that dangling membership is exactly
        # what trips "Object of type <Edition> not in session" as an SAWarning-promoted error.
        edition, _created = insert_or_requery(
            session,
            Edition(
                work_id=work_id,
                isbn_13=isbn_13,
                format=fmt,
                page_count=page_count,
                audio_minutes=audio_minutes,
                publication_date=publication_date,
                narrators=narrator_objs,
            ),
            lambda: session.query(Edition).filter_by(work_id=work_id, format=fmt).first(),
        )
        session.flush()  # Ensure edition.id is populated for the caller's ReadingHistory check
    else:
        if apply_metadata:
            # Update existing edition if new metadata found (publication_date intentionally
            # not updated on existing editions — original behavior preserved).
            edition.isbn_13 = isbn_13 or edition.isbn_13
            edition.page_count = page_count or edition.page_count
            edition.audio_minutes = audio_minutes or edition.audio_minutes
        if narrator_objs:
            edition.narrators = list(set(edition.narrators) | set(narrator_objs))

    return edition


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


def collect_embedding_texts(row: dict) -> list[str]:
    """Every text persist_enriched_work will pass to standardize_trope/standardize_style for
    this row — trope names, author/work/narrator style strings, plus the cleaned genre∪mood
    fallback tags IF (and only if) persist would actually fall back to them (no real
    enriched_tropes AND the caller hasn't opted out via write_fallback_tropes=False). Used to
    warm the get_cached_embedding LRU BEFORE a write session opens (GH #123) so the in-session
    standardize_* calls become cache hits instead of network embeds."""
    texts: list[str] = []

    enriched_tropes = _nan_to_list(row.get("enriched_tropes"))
    for t_data in enriched_tropes:
        name = t_data.get("trope_name")
        if isinstance(name, str) and name.strip():
            texts.append(name)

    for style_data in (row.get("author_style"), row.get("work_style")):
        for _attr_type, style_name in _iter_style_items(style_data, "warm"):
            texts.append(style_name)
    narrator_styles = row.get("narrator_styles")
    if isinstance(narrator_styles, dict):
        for n_style_data in narrator_styles.values():
            for _attr_type, style_name in _iter_style_items(n_style_data, "warm"):
                texts.append(style_name)

    if not enriched_tropes and row.get("write_fallback_tropes", True):
        genres = clean_genres(_nan_to_list(row.get("genres")))
        moods = clean_moods(_nan_to_list(row.get("moods")))
        for tag in set(genres) | set(moods):
            texts.extend(clean_trope_name(tag))

    return texts


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

    # 2. Work lookup. Moved above contributor materialization (#96) so the existing-work branch
    # can merge desired contributors instead of relying on WorkContributor objects built before we
    # know whether this is a new or existing work. no_autoflush guards this query against
    # autoflushing other pending session state (e.g. an Author added earlier in this same call for
    # a prior row in a batch import) mid-lookup.
    with session.no_autoflush:
        work = (
            session.query(Work)
            .join(WorkContributor)
            .join(Author)
            .filter(Work.title == row["Title"])
            .filter(Author.name == (row.get("Author_1") or row.get("Author")))
            .first()
        )

    # Resolve/Create Authors and collect (author, role) pairs to link below. Author creation
    # happens only for entries that will actually be linked to the work (both the new-work and
    # existing-work branches below link every desired pair), so orphan Authors can no longer
    # result from any prod path (two_phase/imports always set skip_enrichment=False); the
    # Dagster ETL's skip_enrichment=True branch can still flush an unlinked Author for an
    # existing work — acceptable for operator-curated re-runs, listed in PR-D's dry-run.
    desired: list[tuple[Author, str]] = []
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
        # Case-insensitive lookup so a casing variant ("casualfarmer" vs "Casualfarmer") reuses the
        # existing row instead of creating a duplicate Author (complements the dedup backfill).
        author = session.query(Author).filter(func.lower(Author.name) == name.lower()).first()
        if not author:
            # GH #95: uq_authors_name_lower fires on lower(name), which an exact filter_by
            # would miss for a case-variant race winner — insert_or_requery's recovery path
            # reuses this same case-insensitive lookup instead.
            author, _created = insert_or_requery(
                session,
                Author(name=name),
                lambda name=name: session.query(Author).filter(func.lower(Author.name) == name.lower()).first(),
            )

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

        desired.append((author, role))

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

    if not work:
        work = Work(
            title=row["Title"],
            contributors=[WorkContributor(author=a, role=r) for a, r in desired],
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
        # GH #96: link newly discovered contributors (deep pass / re-import). Previously the
        # WorkContributor objects dangled off Author.contributions and SQLAlchemy 2.0's removed
        # backref-cascade silently never flushed them (SAWarning) — co-authors were lost and
        # their Author rows orphaned. Mirror the narrator merge below.
        existing_pairs = {(c.author_id, c.role) for c in work.contributors}
        for author, role in desired:
            if (author.id, role) not in existing_pairs:
                work.contributors.append(WorkContributor(author=author, role=role))

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

    # 3. Edition & Narrators (shared with two_phase.complete_edition — history-format-edit)
    edition = merge_edition_and_narrators(
        session,
        work_id=work.id,
        fmt=row.get("format"),
        isbn_13=isbn_13,
        page_count=page_count,
        audio_minutes=audio_minutes,
        publication_date=publication_date,
        narrator_names=row.get("narrator_names"),
        narrator_styles=row.get("narrator_styles"),
        style_manager=style_manager,
        apply_metadata=not row.get("skip_enrichment"),
    )

    # 4. Reading History (The actual read event). pd.NaT/NaN are truthy, so guard with pd.isna
    # before calling .date() (which would raise on NaT).
    _raw_date = row.get("date_completed")
    date_completed = pd.to_datetime(_raw_date).date() if _raw_date is not None and not pd.isna(_raw_date) else None

    if date_completed:
        user_id = get_required_user_id()  # per-user: a friend re-reading my book is not a duplicate (ADR-048)
        # GH #95: uq_reading_history_user_edition_date backstops this guard against a
        # concurrent same-read-event race; get_or_create's filters are an exact match for
        # the constraint's columns, so no case-insensitive requery is needed here.
        get_or_create(
            session,
            ReadingHistory,
            edition_id=edition.id,
            date_completed=date_completed,
            user_id=user_id,
            defaults={"user_rating": user_rating, "user_notes": user_notes},
        )

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
            # Fallback genre/mood tropes are a stopgap ONLY for a work with no real (scout) trope, and
            # only when the caller wants them — the two-phase fast pass opts out (write_fallback_tropes
            # =False) because its deep pass supplies the real tropes (Spec #65, 2026-06-23). Cleaned the
            # same way as genres/moods so a fallback can never write a UUID-tailed / unsplit slug.
            # GH #111: "has a real trope" = any linked trope whose cleaned name is NOT a
            # re-encoding of this work's genres/moods (the shared #69 predicate) — the old
            # `justification IS NOT NULL` heuristic misclassified real attractor tropes.
            linked = (
                session.query(Trope.name)
                .join(WorkTrope, WorkTrope.trope_id == Trope.id)
                .filter(WorkTrope.work_id == work.id)
                .all()
            )
            has_real_trope = any(is_fallback_trope_name(name, work.genres, work.moods) is False for (name,) in linked)
            if row.get("write_fallback_tropes", True) and not has_real_trope:
                for tag in all_fallback_tags:
                    for name in clean_trope_name(tag):
                        # #70: fallback tags map by EXACT cleaned name only — never
                        # standardize_trope's 0.85 semantic match, which is how mood tags like
                        # "Dark" landed on real tropes ("The Dark Night of the Soul") and
                        # flattened every user's trope fingerprint.
                        standardized_trope = _safe_standardize(
                            trope_manager.get_or_create_fallback_trope, name, label=f"trope {name!r}"
                        )
                        if standardized_trope is None:
                            continue
                        existing_link = (
                            session.query(WorkTrope).filter_by(work_id=work.id, trope_id=standardized_trope.id).first()
                        )
                        if not existing_link:
                            session.add(WorkTrope(work=work, trope=standardized_trope))

    return work
