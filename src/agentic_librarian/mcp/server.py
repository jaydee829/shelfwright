from __future__ import annotations

from datetime import date
from uuid import UUID

import numpy as np
from agentic_librarian.core.user_context import get_required_user_id
from agentic_librarian.db.models import (
    Author,
    AuthorStyle,
    Edition,
    ReadingHistory,
    Style,
    Suggestions,
    Trope,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("agentic_librarian")

# Initialize DatabaseManager (ADR-006)
db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager):
    """Override the global db_manager (primarily for testing)."""
    global db_manager
    db_manager = new_manager


def _parse_uuid(value) -> UUID | None:
    """Validate an agent-supplied id as a UUID; None on anything else (SEC-002).
    Agents may pass titles or garbage where ids belong (REC-016) — never let that
    reach a psycopg2 UUID cast."""
    if not value:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value).strip())
    except (ValueError, TypeError):
        return None


def _normalize_status(value, allowed: tuple[str, ...]) -> str | None:
    """Case-insensitively match an agent-supplied status to a canonical member of
    `allowed`; None if it matches nothing (SEC-002: strict enum, no coercion)."""
    if not isinstance(value, str):
        return None
    needle = value.strip().lower()
    for canonical in allowed:
        if canonical.lower() == needle:
            return canonical
    return None


@mcp.tool()
def get_server_status() -> str:
    """Check if the Librarian MCP server is running and connected to DB."""
    try:
        with db_manager.get_session() as session:
            session.execute(select(1))
        return "Librarian MCP Server is online and DB connected."
    except Exception as e:
        return f"Librarian MCP Server error: {str(e)}"


@mcp.tool()
def search_internal_database(target_tropes: list[str], target_styles: list[str] = None, limit: int = 10) -> list[dict]:
    """
    Performs a pgvector similarity search across tropes and literary styles.
    """
    with db_manager.get_session() as session:
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)

        candidate_work_ids = set()
        avg_vector = None

        # 1. Trope Search
        if target_tropes:
            embeddings = [tm._get_embedding(t) for t in target_tropes]
            avg_vector = np.mean(embeddings, axis=0).tolist()
            similar_tropes = session.query(Trope).order_by(Trope.embedding.cosine_distance(avg_vector)).limit(5).all()
            trope_ids = [t.id for t in similar_tropes]
            trope_works = (
                session.query(Work.id).join(WorkTrope).filter(WorkTrope.trope_id.in_(trope_ids)).limit(limit).all()
            )
            candidate_work_ids.update([w[0] for w in trope_works])

        # 2. Style Search
        if target_styles:
            s_embeddings = [sm._get_embedding(s) for s in target_styles]
            avg_style_vector = np.mean(s_embeddings, axis=0).tolist()
            similar_styles = (
                session.query(Style).order_by(Style.embedding.cosine_distance(avg_style_vector)).limit(5).all()
            )
            style_ids = [s.id for s in similar_styles]

            # Check Author, Work, and Narrator styles
            author_works = (
                session.query(Work.id)
                .join(WorkContributor)
                .join(Author)
                .join(AuthorStyle)
                .filter(AuthorStyle.style_id.in_(style_ids))
                .limit(limit)
                .all()
            )
            work_styles = (
                session.query(Work.id).join(WorkStyle).filter(WorkStyle.style_id.in_(style_ids)).limit(limit).all()
            )

            candidate_work_ids.update([w[0] for w in author_works])
            candidate_work_ids.update([w[0] for w in work_styles])

        # 3. Final Work Retrieval, ordered by semantic relevance.
        if not candidate_work_ids:
            return []

        # Order candidates by their closest matching trope to the query vector (cosine
        # distance). Candidates that arrived via style-only matching (no matching trope)
        # are appended afterward in a stable order. Without this, the set + IN filter
        # returns rows in arbitrary DB order.
        ordered_ids: list[UUID] = []
        if target_tropes and avg_vector is not None:
            ranked = (
                session.query(Work.id)
                .join(WorkTrope, WorkTrope.work_id == Work.id)
                .join(Trope, Trope.id == WorkTrope.trope_id)
                .filter(Work.id.in_(list(candidate_work_ids)))
                .group_by(Work.id)
                .order_by(func.min(Trope.embedding.cosine_distance(avg_vector)))
                .limit(limit)
                .all()
            )
            ordered_ids = [w[0] for w in ranked]
        # sorted() so the style-only leftovers have a deterministic order (set iteration
        # order is process-randomized).
        for wid in sorted(candidate_work_ids):
            if wid not in ordered_ids:
                ordered_ids.append(wid)
        ordered_ids = ordered_ids[:limit]

        # Eager load contributors/authors, then restore the ranked order.
        works = (
            session.query(Work)
            .options(joinedload(Work.contributors).joinedload(WorkContributor.author))
            .filter(Work.id.in_(ordered_ids))
            .all()
        )
        works_by_id = {w.id: w for w in works}
        ordered_works = [works_by_id[wid] for wid in ordered_ids if wid in works_by_id]

        return [
            {
                "id": str(w.id),
                "title": w.title,
                "authors": [c.author.name for c in w.contributors],
                "genres": w.genres,
                "description": w.description,
            }
            for w in ordered_works
        ]


@mcp.tool()
def get_unacted_suggestions(target_tropes: list[str], target_styles: list[str] = None, limit: int = 5) -> list[dict]:
    """
    Pulls previous recommendations that were never read or ignored,
    ranked by similarity to current target vibes.
    """
    with db_manager.get_session() as session:
        # 1. Get all unacted suggestions with Eager Loading (Fixes N+1)
        query = (
            session.query(Suggestions)
            .filter(Suggestions.status == "Suggested")
            .options(
                joinedload(Suggestions.work).options(
                    selectinload(Work.tropes).joinedload(WorkTrope.trope),
                    selectinload(Work.styles).joinedload(WorkStyle.style),
                    selectinload(Work.contributors)
                    .joinedload(WorkContributor.author)
                    .selectinload(Author.styles)
                    .joinedload(AuthorStyle.style),
                )
            )
        )
        suggestions = query.all()

        if not suggestions:
            return []

        # 2. Rank them semantically if targets are provided
        if not target_tropes and not target_styles:
            return [
                {
                    "id": str(s.work.id),
                    "title": s.work.title,
                    "justification": s.justification,
                    "suggested_at": s.suggested_at.isoformat() if s.suggested_at else None,
                }
                for s in suggestions[:limit]
            ]

        # Use TropeManager/StyleManager to get embeddings for ranking
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)

        target_trope_vec = None
        if target_tropes:
            t_embeddings = [tm._get_embedding(t) for t in target_tropes]
            target_trope_vec = np.mean(t_embeddings, axis=0)

        target_style_vec = None
        if target_styles:
            s_embeddings = [sm._get_embedding(s) for s in target_styles]
            target_style_vec = np.mean(s_embeddings, axis=0)

        def score_suggestion(s):
            score = 0
            # Score by tropes linked to this suggestion's work
            if target_trope_vec is not None and s.work.tropes:
                work_trope_vecs = [
                    np.array(wt.trope.embedding) for wt in s.work.tropes if wt.trope.embedding is not None
                ]
                if work_trope_vecs:
                    avg_work_trope = np.mean(work_trope_vecs, axis=0)
                    score += np.dot(target_trope_vec, avg_work_trope)  # Cosine similarity assumes normalized

            # Score by styles linked to this suggestion's work or author
            if target_style_vec is not None:
                style_links = list(s.work.styles)
                # Primary author styles
                primary_contributor = next((c for c in s.work.contributors if c.role == "Author"), None)
                if primary_contributor:
                    style_links.extend(primary_contributor.author.styles)

                work_style_vecs = [np.array(sl.style.embedding) for sl in style_links if sl.style.embedding is not None]
                if work_style_vecs:
                    avg_work_style = np.mean(work_style_vecs, axis=0)
                    score += np.dot(target_style_vec, avg_work_style)

            return score

        ranked = sorted(suggestions, key=score_suggestion, reverse=True)

        return [
            {
                "id": str(s.work.id),
                "title": s.work.title,
                "justification": s.justification,
                "suggested_at": s.suggested_at.isoformat() if s.suggested_at else None,
            }
            for s in ranked[:limit]
        ]


@mcp.tool()
def check_reading_history(title: str, author: str) -> dict:
    """Checks if a book has been read and determines re-read eligibility."""
    with db_manager.get_session() as session:
        entry = (
            session.query(ReadingHistory)
            .join(Edition)
            .join(Work)
            .join(WorkContributor)
            .join(Author)
            .filter(Work.title == title)
            .filter(Author.name == author)
            .order_by(ReadingHistory.date_completed.desc())
            .first()
        )

        if entry:
            completion_date = entry.date_completed
            today = date.today()
            delta = today - completion_date
            years_since = delta.days / 365.25

            return {
                "status": "Read",
                "date_completed": completion_date.isoformat(),
                "years_since_completion": round(years_since, 2),
                "is_re_read_candidate": years_since > 2.0,
                "rating": entry.user_rating,
            }
        return {"status": "Unread", "is_re_read_candidate": True}


_READING_STATUSES = ("read",)


def _valid_name(value, max_len: int = 500) -> bool:
    """Non-empty string within length bounds — for agent-supplied titles/authors (SEC-002)."""
    return isinstance(value, str) and bool(value.strip()) and len(value) <= max_len


@mcp.tool()
def update_reading_status(title: str, author: str, status: str, notes: str | None = None) -> str:
    """Updates history based on feedback (e.g. 'I read that years ago')."""
    if not _valid_name(title):
        return "Error: title must be a non-empty string of at most 500 characters."
    if not _valid_name(author):
        return "Error: author must be a non-empty string of at most 500 characters."
    canonical = _normalize_status(status, _READING_STATUSES)
    if canonical is None:
        # Previously any unknown status returned success while writing NOTHING (silent
        # false-success). Reject honestly instead (SEC-002).
        return f"Error: status must be one of {', '.join(_READING_STATUSES)}; got {status!r}."
    notes = notes[:2000] if isinstance(notes, str) else None
    user_id = get_required_user_id()  # before try: unset context must raise, not soft-fail (ADR-048)
    try:
        with db_manager.get_session() as session:
            # Find the work/edition first
            work = (
                session.query(Work)
                .join(WorkContributor)
                .join(Author)
                .filter(Work.title == title, Author.name == author)
                .first()
            )
            if not work:
                return f"Work '{title}' by {author} not found in database."

            # Find first edition
            edition = session.query(Edition).filter_by(work_id=work.id).first()
            if not edition:
                # Create a placeholder edition if none exists
                edition = Edition(work=work, format="Unknown")
                session.add(edition)
                session.flush()

            if canonical == "read":
                history = ReadingHistory(
                    edition=edition,
                    user_id=user_id,
                    date_completed=date.today(),  # Placeholder for manual addition
                    user_notes=notes,
                )
                session.add(history)

            session.flush()  # ADR-016
            return f"Successfully updated status for '{title}' to {status}."
    except Exception as e:
        return f"Error updating status: {str(e)}"


@mcp.tool()
def add_book_to_history(
    title: str,
    author: str,
    date_completed: str | None = None,
    rating: int | None = None,
    format: str = "ebook",
    notes: str | None = None,
) -> str:
    """Add ONE book to the reading history (single-title import). Enriches + persists the
    work first if it isn't in the catalog (runs the scouts — takes a minute or two), then
    logs a READ EVENT. History is a log of read events: a re-read (different completion
    date) inserts a new row; the same work+date is a duplicate and is not double-logged.
    date_completed defaults to today (the Phase-4 UI will auto-fill it visibly)."""
    if not _valid_name(title):
        return "Error: title must be a non-empty string of at most 500 characters."
    if not _valid_name(author):
        return "Error: author must be a non-empty string of at most 500 characters."
    if date_completed is None:
        completed = date.today()
    else:
        try:
            completed = date.fromisoformat(str(date_completed))
        except ValueError:
            return f"Error: date_completed must be ISO YYYY-MM-DD; got {date_completed!r}."
        if completed > date.today():
            return f"Error: date_completed {completed.isoformat()} is in the future."
    # bool is an int subclass — reject it explicitly so rating=True can't slip in as 1.
    if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5):
        return f"Error: rating must be an integer from 1 to 5; got {rating!r}."
    format = (format or "ebook")[:50]
    notes = notes[:2000] if isinstance(notes, str) else None
    user_id = get_required_user_id()  # before try: unset context must raise, not soft-fail (ADR-048)

    work_id = enrich_and_persist_work(title=title, author=author, format=format)
    if work_id is None:
        return f"Error: could not resolve '{title}' by {author} — check the spelling, or the scouts found nothing."

    try:
        with db_manager.get_session() as session:
            uuid_obj = _parse_uuid(work_id)
            # Duplicate guard FIRST (PR #37 review): on the no-op path nothing may be
            # created — not even an Edition (the session commits on clean exit).
            prior_reads = session.query(ReadingHistory).join(Edition).filter(Edition.work_id == uuid_obj).all()
            if any(r.date_completed == completed for r in prior_reads):
                return f"'{title}' is already logged as completed {completed.isoformat()}. No new entry written."
            edition = session.query(Edition).filter_by(work_id=uuid_obj, format=format).first()
            if not edition:
                edition = Edition(work_id=uuid_obj, format=format)
                session.add(edition)
                session.flush()
            session.add(
                ReadingHistory(
                    edition_id=edition.id,
                    user_id=user_id,
                    date_completed=completed,
                    user_rating=rating,
                    user_notes=notes,
                )
            )
            session.flush()
            return f"Added '{title}' to your reading history (work {work_id}, read #{len(prior_reads) + 1})."
    except Exception as e:
        return f"Error adding to reading history: {str(e)}"


@mcp.tool()
def log_suggestion(work_id: str, context: str, justification: str, conversation_id: str | None = None) -> str:
    """Logs a new recommendation to the Suggestions table."""
    uuid_obj = _parse_uuid(work_id)
    if uuid_obj is None:
        return f"Error: work_id must be a valid UUID, got {work_id!r}."
    user_id = get_required_user_id()  # before try: unset context must raise, not soft-fail (ADR-048)
    try:
        with db_manager.get_session() as session:
            # SEC-002 referent check: a suggestion must point at a real catalog work.
            if session.get(Work, uuid_obj) is None:
                return f"Error: no work exists with id {work_id}."
            suggestion = Suggestions(
                work_id=uuid_obj,
                user_id=user_id,
                context=(context or "")[:200],
                justification=(justification or "")[:2000],
                conversation_id=_parse_uuid(conversation_id),
                status="Suggested",
            )
            session.add(suggestion)
            session.flush()
            return f"Logged suggestion for work {work_id}."
    except Exception as e:
        return f"Error logging suggestion: {str(e)}"


_SUGGESTION_STATUSES = ("Accepted", "Dismissed", "Already Read")


@mcp.tool()
def update_suggestion_status(work_id: str, status: str) -> str:
    """
    Updates the status of a suggestion (e.g. 'Accepted', 'Dismissed', 'Already Read').
    This ensures unacted suggestions are cleaned up based on feedback.
    """
    uuid_obj = _parse_uuid(work_id)
    if uuid_obj is None:
        return f"Error: work_id must be a valid UUID, got {work_id!r}."
    canonical = _normalize_status(status, _SUGGESTION_STATUSES)
    if canonical is None:
        return f"Error: status must be one of {', '.join(_SUGGESTION_STATUSES)}; got {status!r}."
    try:
        with db_manager.get_session() as session:
            suggestion = (
                session.query(Suggestions)
                .filter_by(work_id=uuid_obj, status="Suggested")
                .order_by(Suggestions.suggested_at.desc())
                .first()
            )
            if not suggestion:
                return f"No active suggestion found for work {work_id}."

            suggestion.status = canonical
            session.flush()
            return f"Updated suggestion for work {work_id} to status: {canonical}."
    except Exception as e:
        return f"Error updating suggestion status: {str(e)}"


@mcp.tool()
def get_user_trope_preferences(limit: int = 20) -> list[str]:
    """Aggregates frequent tropes from user's history."""
    with db_manager.get_session() as session:
        # Find tropes present in books read by user
        results = (
            session.query(Trope.name, func.count(WorkTrope.work_id))
            .join(WorkTrope)
            .join(Work)
            .join(Edition)
            .join(ReadingHistory)
            .group_by(Trope.name)
            .order_by(func.count(WorkTrope.work_id).desc())
            .limit(limit)
            .all()
        )
        return [r[0] for r in results]


@mcp.tool()
def get_work_details(work_id: str) -> dict:
    """Returns metadata, tropes, and merged style profile for a work."""
    # Web-discovered candidates have no DB id; an agent may pass a title instead of a
    # UUID. Guard the lookup so a bad work_id returns no details rather than crashing the
    # run (the psycopg2 UUID cast would otherwise raise). Resolving discoveries to DB
    # works / enriching new ones is Spec 4.
    uuid_obj = _parse_uuid(work_id)
    if uuid_obj is None:
        return {}

    with db_manager.get_session() as session:
        work = session.query(Work).filter_by(id=uuid_obj).first()
        if not work:
            return {}

        tropes = [
            {
                "name": wt.trope.name,
                "description": wt.trope.description,
                "relevance": wt.relevance_score,
                "justification": wt.justification,
            }
            for wt in work.tropes
        ]

        # Style Inheritance/Override Logic:
        # 1. Start with Work-specific styles
        merged_styles = {ws.attribute_type: ws.style.name for ws in work.styles}

        # 2. Inherit from Primary Author for missing attributes
        # Find primary author (role='Author' or first contributor)
        primary_contributor = next((c for c in work.contributors if c.role == "Author"), None)
        if not primary_contributor and work.contributors:
            primary_contributor = work.contributors[0]

        if primary_contributor:
            author = primary_contributor.author
            for ads in author.styles:
                if ads.attribute_type not in merged_styles:
                    merged_styles[ads.attribute_type] = ads.style.name

        return {
            "title": work.title,
            "description": work.description,
            "genres": work.genres,
            "tropes": tropes,
            "styles": merged_styles,
        }


def _normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _normalized_col(col):
    """SQL-side equivalent of _normalize: lowercase, collapse internal whitespace, trim — so
    de-dup matches even when a stored title/name has irregular spacing."""
    return func.trim(func.regexp_replace(func.lower(col), r"\s+", " ", "g"))


@mcp.tool()
def enrich_and_persist_work(title: str, author: str, format: str = "ebook") -> str | None:
    """De-dup a web-discovered book against the catalog; if new, enrich it via the ScoutManager
    and persist it as a Work (no reading history). Returns the work_id, or None if enrichment
    found nothing. This is the single write surface for discoveries — a future authorization
    layer (SEC-002) wraps here."""
    # SEC-002: this is a write path fed by web-derived strings — validate shape upfront.
    if not _valid_name(title):
        print(f"Warning: enrich_and_persist_work rejected invalid title {title!r}")
        return None
    if not _valid_name(author):
        print(f"Warning: enrich_and_persist_work rejected invalid author {author!r}")
        return None
    format = (format or "ebook")[:50]
    try:
        with db_manager.get_session() as session:
            # 1. De-dup (Case 1): match an existing Work by normalized title + author.
            existing = (
                session.query(Work)
                .join(WorkContributor)
                .join(Author)
                .filter(_normalized_col(Work.title) == _normalize(title))
                .filter(_normalized_col(Author.name) == _normalize(author))
                .first()
            )
            if existing:
                return str(existing.id)

            # 2. Enrich (Case 2): run the scouts, then persist via the shared function.
            from agentic_librarian.orchestration.definitions import create_scout_manager

            enriched = create_scout_manager().enrich(title=title, author=author, format=format)
            if not enriched:
                return None

            row = {
                "Title": title,
                "Author_1": author,
                "format": format,
                "skip_enrichment": False,
                "date_completed": None,
                **enriched,
                "genres": list(enriched.get("genres") or []),
                "moods": list(enriched.get("moods") or []),
            }
            tm = TropeManager(session=session)
            sm = StyleManager(session=session)
            work = persist_enriched_work(session, row, tm, sm)
            if work is None:
                return None
            session.flush()  # ensure work.id is populated
            # get_session commits on clean exit (matches the other write tools) — no explicit commit.
            return str(work.id)
    except Exception as e:  # noqa: BLE001 - degrade gracefully, never crash the pipeline
        print(f"enrich_and_persist_work error: {e}")
        return None


if __name__ == "__main__":
    mcp.run()
