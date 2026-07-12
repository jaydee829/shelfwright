"""Two-phase enrichment service (Lift 2 Stage 3).

Fast pass: API scouts only (seconds) — persist the Work + log the read immediately.
Deep pass: the slow LLM scouts, run later by the Cloud Tasks internal endpoint, which
re-persists (updates) the SAME Work. Reuses the shared persist_enriched_work so the
catalog is built identically to the ETL and discovery paths (DRY).

Both passes run their scouts (external HTTP/LLM calls) with NO database session held
(GH #94) — a short read session first, then the scouts, then a fresh write session that
re-checks dedup before persisting (the #95 TOCTOU window is back to milliseconds instead
of the scouts' full duration).

This is a parallel surface to mcp/server.py's enrich_and_persist_work (the all-scouts
discovery write tool, left untouched): same persist core, tiered scouts."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func

from agentic_librarian.core.user_context import get_required_user_id
from agentic_librarian.db.models import Author, Edition, ReadingHistory, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work
from agentic_librarian.orchestration.definitions import (
    create_deep_scout_manager,
    create_fast_scout_manager,
)
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager

# Module-level fallback pool for non-API processes; the API lifespan injects its shared
# manager via set_db_manager (GH #102 consolidation — the old "deferred to Stage 4" note was stale).
db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (for tests) — the mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


def _normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _normalized_col(col):
    """SQL-side equivalent of _normalize: lowercase, collapse whitespace, trim."""
    return func.trim(func.regexp_replace(func.lower(col), r"\s+", " ", "g"))


def _run_scouts(manager, *, title: str, author: str, fmt: str, write_fallback_tropes: bool = True) -> dict | None:
    """Run a scout tier with NO session held (GH #94 — the external HTTP/LLM calls used
    to pin a pooled connection idle-in-transaction for their whole duration). Returns the
    persist-ready row dict, or None if the scouts found nothing. date_completed=None so
    persist writes NO reading_history (the read-event is logged separately)."""
    enriched = manager.enrich(title=title, author=author, format=fmt)
    if not enriched:
        return None
    return {
        "Title": title,
        "Author_1": author,
        "format": fmt,
        "skip_enrichment": False,
        "date_completed": None,
        **enriched,
        "genres": list(enriched.get("genres") or []),
        "moods": list(enriched.get("moods") or []),
        # after **enriched so a scout payload can never clobber the caller's choice
        "write_fallback_tropes": write_fallback_tropes,
    }


def _persist_row(session, row: dict) -> Work | None:
    """Persist a scouted row via the shared function (the only part needing a session)."""
    return persist_enriched_work(session, row, TropeManager(session=session), StyleManager(session=session))


def enrich_fast(title: str, author: str, fmt: str = "ebook") -> tuple[UUID, bool] | None:
    """Fast pass: de-dup against the catalog; if new, run the API scouts (no session
    held, #94) and persist in a fresh session that RE-CHECKS the dedup (a concurrent
    import may have inserted the work while the scouts ran — the #95 TOCTOU window is
    back to milliseconds). Returns (work_id, created), or None if the scouts found
    nothing. Logs NO reading_history (see add_read_event)."""
    fmt = (fmt or "ebook")[:50]

    def _find_existing(session):
        return (
            session.query(Work)
            .join(WorkContributor)
            .join(Author)
            .filter(_normalized_col(Work.title) == _normalize(title))
            .filter(_normalized_col(Author.name) == _normalize(author))
            .first()
        )

    with db_manager.get_session() as session:
        existing = _find_existing(session)
        if existing:
            return existing.id, False  # UUID scalar — safe after close

    row = _run_scouts(create_fast_scout_manager(), title=title, author=author, fmt=fmt, write_fallback_tropes=False)
    if row is None:
        return None

    with db_manager.get_session() as session:
        existing = _find_existing(session)  # dedup re-check (#94/#95)
        if existing:
            return existing.id, False
        work = _persist_row(session, row)
        if work is None:
            return None
        session.flush()
        return work.id, True


def add_read_event(work_id: UUID, *, completed, rating: int | None, notes: str | None, fmt: str) -> dict:
    """Log a read-event for the current user against work_id (the existing
    add_book_to_history semantics: a re-read on a new date is a new row; the same
    work+date is a no-op). Requires user context (as_user / the auth dependency)."""
    user_id = get_required_user_id()
    with db_manager.get_session() as session:
        prior_reads = (
            session.query(ReadingHistory)
            .join(Edition)
            .filter(Edition.work_id == work_id, ReadingHistory.user_id == user_id)
            .all()
        )
        if any(r.date_completed == completed for r in prior_reads):
            return {"read_number": len(prior_reads), "already_logged": True}
        edition = session.query(Edition).filter_by(work_id=work_id, format=fmt).first()
        if not edition:
            edition = Edition(work_id=work_id, format=fmt)
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
        return {"read_number": len(prior_reads) + 1, "already_logged": False}


def enrich_deep(work_id: UUID) -> bool:
    """Deep pass (Cloud Tasks target): read the Work's identity in a short session, run
    the slow LLM scouts with NO session held (#94 — previously minutes idle-in-transaction
    at enrich-queue concurrency 4, where a late transient failure also re-paid every LLM
    call), then re-persist in a fresh session. Returns False if no Work has that id."""
    with db_manager.get_session() as session:
        work = session.get(Work, work_id)
        if work is None:
            return False
        author = next((c.author.name for c in work.contributors if c.role == "Author"), None)
        if author is None:
            return False
        title = work.title  # scalars captured before close (detached-instance rule)
        fmt = work.editions[0].format if work.editions else "ebook"

    row = _run_scouts(create_deep_scout_manager(), title=title, author=author, fmt=fmt)
    if row is None:
        return True  # scouts found nothing to add; the task is done, not retryable

    with db_manager.get_session() as session:
        _persist_row(session, row)
        session.flush()
    return True
