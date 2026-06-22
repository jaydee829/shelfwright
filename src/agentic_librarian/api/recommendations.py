"""Recommendations surface (Lift 2 Stage 2). Reads the user's active Suggestions
(Lift 1 table) and lets them dismiss one. The '✓ I read this' → Read transition runs
through the add-book flow (Stage 3), so this endpoint accepts only 'Dismissed' for now.
Identity comes from the auth context; rows are filtered by user.id (ADR-048)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import joinedload

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.db.models import Edition, ReadingHistory, Suggestions, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager

router = APIRouter()
db_manager = DatabaseManager()

# Stage 3 wires the '✓ I read this' flow (add-book → status Read); 'Dismissed' = 'Not for me'.
ALLOWED_STATUS_UPDATES = {"Dismissed", "Read"}


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (for tests) — the mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


@router.get("/recommendations")
def get_recommendations(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        rows = (
            session.query(Suggestions)
            .filter(Suggestions.user_id == user.id, Suggestions.status == "Suggested")  # my active picks
            .options(
                joinedload(Suggestions.work).selectinload(Work.contributors).joinedload(WorkContributor.author),
            )
            .order_by(Suggestions.suggested_at.desc())
            .all()
        )
        work_ids = [s.work_id for s in rows]
        read_by_work: dict = {}
        if work_ids:
            rh_rows = (
                session.query(ReadingHistory, Edition.work_id)
                .join(Edition)
                .filter(Edition.work_id.in_(work_ids), ReadingHistory.user_id == user.id)
                .order_by(ReadingHistory.date_completed.desc())
                .all()
            )
            for rh, wid in rh_rows:
                read_by_work.setdefault(wid, rh)  # first per work = latest (date-desc)

        def _read_fields(work_id: UUID) -> dict:
            rh = read_by_work.get(work_id)
            if rh is None:
                return {"read_status": "new", "last_read": None, "rating": None}
            return {
                "read_status": "reread",
                "last_read": rh.date_completed.isoformat(),
                "rating": rh.user_rating,
            }

        return [
            {
                "id": str(s.id),
                "work_id": str(s.work_id),
                "title": s.work.title,
                "authors": [c.author.name for c in s.work.contributors if c.role == "Author"],
                "justification": s.justification,
                "context": s.context,
                "suggested_at": s.suggested_at.isoformat() if s.suggested_at else None,
                "status": s.status,
                "genres": s.work.genres or [],
                **_read_fields(s.work_id),
            }
            for s in rows
        ]


@router.post("/recommendations/{suggestion_id}/status")
def set_recommendation_status(
    suggestion_id: UUID,
    status: str = Body(..., embed=True),
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    if status not in ALLOWED_STATUS_UPDATES:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(ALLOWED_STATUS_UPDATES)}")
    with db_manager.get_session() as session:
        sug = (
            session.query(Suggestions)
            .filter(Suggestions.id == suggestion_id, Suggestions.user_id == user.id)  # scoping: only mine
            .first()
        )
        if sug is None:
            raise HTTPException(status_code=404, detail="suggestion not found")
        sug.status = status
        session.flush()
    return {"id": str(suggestion_id), "status": status}
