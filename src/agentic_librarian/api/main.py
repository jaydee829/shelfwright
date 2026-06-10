from fastapi import Body, Depends, FastAPI, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import joinedload, selectinload

from agentic_librarian.agents.runtime import LibrarianConversation, astart_conversation
from agentic_librarian.api.analysis import router as analysis_router
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.books import router as books_router
from agentic_librarian.api.recommendations import router as recommendations_router
from agentic_librarian.chat import stream, transcript
from agentic_librarian.core.user_context import as_user
from agentic_librarian.db.models import (
    Edition,
    ReadingHistory,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager

app = FastAPI(title="Agentic Librarian API")
db_manager = DatabaseManager()
# NOTE: several modules each own a lazy DatabaseManager (this module, api/auth.py,
# chat/transcript.py, core/usage.py) — ~4 pools. Acceptable at friends-scale; consolidating
# into one shared manager is deferred to Lift 2 Stage 4 (cleanups), per the Stage 1 final review.
app.include_router(recommendations_router)
app.include_router(analysis_router)
app.include_router(books_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/db")
def db_health_check(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    try:
        with db_manager.get_session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "connected"}
    except Exception as e:
        # 503 so platform health probes and monitors see the failure (HTTP status,
        # not body, is what they key on). Detail is safe: the service is IAM-gated.
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


@app.get("/history")
def get_history(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        # Query reading history with eager loading for efficiency
        history_entries = (
            session.query(ReadingHistory)
            .join(Edition)
            .join(Work)
            .filter(ReadingHistory.user_id == user.id)  # my history, not the commons (ADR-048)
            .options(
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .joinedload(Work.contributors)
                .joinedload(WorkContributor.author)
            )
            .order_by(ReadingHistory.date_completed.desc())
            .all()
        )

        return [
            {
                "id": str(h.id),
                "title": h.edition.work.title,
                "authors": [
                    c.author.name for c in h.edition.work.contributors if c.role == "Author"
                ],  # ETL always writes role="Author"
                "date_completed": h.date_completed.isoformat()
                if h.date_completed
                else None,  # schema forbids NULL; guard is defensive only
                "rating": h.user_rating,
                "format": h.edition.format,
            }
            for h in history_entries
        ]


@app.get("/works")
def get_works(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    """Enriched catalog listing — the walking skeleton's payload (Lift 0)."""
    with db_manager.get_session() as session:
        # selectinload for the collections: joinedload + LIMIT mis-paginates
        # (the limit would apply to joined rows, not works).
        works = (
            session.query(Work)
            .options(
                selectinload(Work.contributors).joinedload(WorkContributor.author),
                selectinload(Work.tropes).joinedload(WorkTrope.trope),
                selectinload(Work.styles).joinedload(WorkStyle.style),
            )
            .order_by(Work.title, Work.id)  # id tiebreaker: stable pages when titles collide
            .offset(offset)
            .limit(limit)
            .all()
        )

        return [
            {
                "id": str(w.id),
                "title": w.title,
                "authors": [
                    c.author.name for c in w.contributors if c.role == "Author"
                ],  # ETL always writes role="Author"
                "publication_year": w.original_publication_year,
                "genres": w.genres or [],
                "moods": w.moods or [],
                "tropes": [wt.trope.name for wt in w.tropes],
                "styles": [{"attribute": ws.attribute_type, "name": ws.style.name} for ws in w.styles],
            }
            for w in works
        ]


# ---------------------------------------------------------------------------
# Chat endpoints (Lift 2)
# ---------------------------------------------------------------------------


async def _open_conversation(*, user_id: str, session_id: str, history: list[dict], on_event) -> LibrarianConversation:
    """Open the mesh conversation for one turn (seam: tests replace this)."""
    return await astart_conversation(user_id=user_id, session_id=session_id, history=history, on_event=on_event)


class _SyncOpener:
    """The conversation object returned by the factory passed to sse_turn. Lazily opens
    the async mesh conversation on first asend — deferred so the open runs inside the
    event loop, not the sync endpoint frame. session_id = conversation_id.hex so usage
    rows (keyed off the ADK session uuid) FK to the transcript row."""

    def __init__(self, user_id, ctx, on_event):
        self._user_id = user_id
        self._ctx = ctx
        self._on_event = on_event
        self._conv = None  # opened lazily on first asend

    async def asend(self, message: str) -> str:
        if self._conv is None:
            self._conv = await _open_conversation(
                user_id=self._user_id,
                session_id=self._ctx.conversation_id.hex,
                history=self._ctx.history,
                on_event=self._on_event,
            )
        return await self._conv.asend(message)

    def close(self):
        if self._conv is not None:
            self._conv.close()


@app.get("/conversations/current")
def get_current_conversation(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with as_user(user.id):
        ctx = transcript.get_or_create_active_conversation()
    return {"id": str(ctx.conversation_id), "messages": ctx.history}


@app.post("/conversations")
def new_conversation(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with as_user(user.id):
        ctx = transcript.start_new_conversation()
    return {"id": str(ctx.conversation_id), "messages": ctx.history}


@app.post("/chat")
def chat(user: AuthenticatedUser = Depends(get_current_user), message: str = Body(..., embed=True)):  # noqa: B008
    with as_user(user.id):
        ctx = transcript.get_or_create_active_conversation()
    adk_user_id = str(user.id)
    return StreamingResponse(
        stream.sse_turn(
            message=message,
            conversation=lambda on_event: _SyncOpener(adk_user_id, ctx, on_event),
            on_persist=lambda role, content: transcript.append_message(ctx.conversation_id, role, content),
            user_id=user.id,
        ),
        media_type="text/event-stream",
    )
