"""POST /availability — batch 'where to get it' + live Libby badge for the visible recs.
ALWAYS 200: links are built purely from the user's saved libraries (never depend on Thunder);
the per-library badge is best-effort (null/[] when Thunder is down or nothing matched)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import joinedload

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.availability import service
from agentic_librarian.availability.links import build_links
from agentic_librarian.db.models import UserLibrary, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager

router = APIRouter()
db_manager = DatabaseManager()

_MAX_WORKS = 50  # a recs page is small; cap to bound upstream work


def set_db_manager(new_manager: DatabaseManager) -> None:
    global db_manager
    db_manager = new_manager


def _authors(work: Work) -> list[str]:
    return [c.author.name for c in work.contributors if c.role == "Author"]


@router.post("/availability")
def get_availability(
    work_ids: list[str] = Body(..., embed=True),  # noqa: B008
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    parsed: list[UUID] = []
    for wid in work_ids[:_MAX_WORKS]:
        try:
            parsed.append(UUID(str(wid)))
        except (ValueError, TypeError):
            continue

    result: dict[str, dict] = {}
    if not parsed:
        return result

    with db_manager.get_session() as session:
        libs = [
            {"slug": r.library_slug, "name": r.display_name}
            for r in session.query(UserLibrary)
            .filter(UserLibrary.user_id == user.id, UserLibrary.provider == "libby")
            .order_by(UserLibrary.sort_order)
            .all()
        ]
        works = (
            session.query(Work)
            .options(joinedload(Work.contributors).joinedload(WorkContributor.author))
            .filter(Work.id.in_(parsed))
            .all()
        )
        # scalars captured before the session closes (detached-instance rule)
        work_rows = [(str(w.id), w.title, (_authors(w) or [""])[0]) for w in works]

    pairs = [(title, author) for _, title, author in work_rows]
    availability = service.batch_availability(db_manager, libs, pairs)

    for wid, title, author in work_rows:
        libby = []
        for lib in libs:
            formats = availability.get((lib["slug"], title, author))
            if formats:  # non-empty match → badge
                libby.append({"library": lib["name"], "slug": lib["slug"], "formats": formats})
        result[wid] = {"links": build_links(title, author, libraries=libs), "libby": libby}
    return result
