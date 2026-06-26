import contextlib
import os
from datetime import date
from uuid import UUID

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.orm import joinedload, selectinload

from agentic_librarian.agents.runtime import LibrarianConversation, astart_conversation
from agentic_librarian.api import analysis, auth, recommendations
from agentic_librarian.api import availability as availability_api
from agentic_librarian.api import imports as imports_api
from agentic_librarian.api import libraries as libraries_api
from agentic_librarian.api.analysis import router as analysis_router
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.availability import router as availability_router
from agentic_librarian.api.books import router as books_router
from agentic_librarian.api.firebase_auth_proxy import router as firebase_auth_proxy_router
from agentic_librarian.api.imports import router as imports_router
from agentic_librarian.api.internal import router as internal_router
from agentic_librarian.api.libraries import router as libraries_router
from agentic_librarian.api.recommendations import router as recommendations_router
from agentic_librarian.chat import stream, transcript
from agentic_librarian.core import usage
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

db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override this module's db_manager (tests / the shared-pool lifespan) — mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Build ONE DatabaseManager at startup and inject it into the API-request-path
    modules that each owned a lazy pool (main, auth, transcript, usage, recommendations,
    analysis) — Lift 2 Stage 4 consolidation. enrichment/two_phase keeps its own pool
    (separate path/test seam). Lazy construction means no DB connection happens here.
    Tests that use TestClient WITHOUT a `with` block skip lifespan and keep their own
    monkeypatched managers."""
    shared = DatabaseManager()
    app.state.db_manager = shared
    set_db_manager(shared)
    auth.set_db_manager(shared)
    transcript.set_db_manager(shared)
    usage.set_db_manager(shared)
    recommendations.set_db_manager(shared)
    analysis.set_db_manager(shared)
    imports_api.set_db_manager(shared)
    availability_api.set_db_manager(shared)
    libraries_api.set_db_manager(shared)
    yield


# Stage 4 opens the Cloud Run IAM gate, making this service publicly reachable (Firebase
# gates the data routes; /health and the SPA are intentionally public). Disable FastAPI's
# auto-docs so the full API schema isn't exposed unauthenticated. (GET /docs etc. now fall
# through to the SPA catch-all, which is harmless.)
app = FastAPI(title="Agentic Librarian API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(firebase_auth_proxy_router)
app.include_router(recommendations_router)
app.include_router(analysis_router)
app.include_router(availability_router)
app.include_router(books_router)
app.include_router(imports_router)
app.include_router(internal_router)
app.include_router(libraries_router)


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


def _history_item(h) -> dict:
    """Serialize one ReadingHistory row to the /history payload shape (shared by GET + PATCH)."""
    work = h.edition.work
    top = sorted(work.tropes, key=lambda wt: wt.relevance_score, reverse=True)[:3]
    return {
        "id": str(h.id),
        "title": work.title,
        "authors": [c.author.name for c in work.contributors if c.role == "Author"],
        "date_completed": h.date_completed.isoformat() if h.date_completed else None,
        "rating": h.user_rating,
        "format": h.edition.format,
        "notes": h.user_notes,
        "genre": work.genres[0] if work.genres else None,
        "tropes": [wt.trope.name for wt in top],
    }


def _history_options():
    """Shared eager-load options for ReadingHistory queries (GET + PATCH)."""
    return [
        joinedload(ReadingHistory.edition)
        .joinedload(Edition.work)
        .joinedload(Work.contributors)
        .joinedload(WorkContributor.author),
        joinedload(ReadingHistory.edition)
        .joinedload(Edition.work)
        .selectinload(Work.tropes)
        .joinedload(WorkTrope.trope),
    ]


@app.get("/history")
def get_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    """Paginated reading log, newest first (INF-029 — mirrors /works)."""
    with db_manager.get_session() as session:
        # Query reading history with eager loading for efficiency
        history_entries = (
            session.query(ReadingHistory)
            .join(Edition)
            .join(Work)
            .filter(ReadingHistory.user_id == user.id)  # my history, not the commons (ADR-048)
            # joinedload on the to-many Work.contributors is safe under LIMIT *here* (unlike
            # /works): the paginated root is ReadingHistory and the collection sits two to-one
            # hops below it, so SQLAlchemy subquery-wraps the LIMIT against ReadingHistory rows.
            # Work.tropes is a to-many so we use selectinload (separate IN query) to avoid
            # cartesian-multiplying rows with the contributors joinedload.
            .options(*_history_options())
            .order_by(ReadingHistory.date_completed.desc(), ReadingHistory.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [_history_item(h) for h in history_entries]


class HistoryUpdate(BaseModel):
    date_completed: date | None = None
    rating: int | None = None
    notes: str | None = None

    @field_validator("rating", mode="before")
    @classmethod
    def _no_bool_rating(cls, v: object) -> object:
        if isinstance(v, bool):
            raise ValueError("rating must be an integer, not a boolean")
        return v

    @field_validator("rating")
    @classmethod
    def _rating_range(cls, v: int | None) -> int | None:
        if v is not None and not 1 <= v <= 5:
            raise ValueError("rating must be from 1 to 5")
        return v

    @field_validator("date_completed")
    @classmethod
    def _not_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("date_completed cannot be in the future")
        return v


@app.delete("/history/{entry_id}")
def delete_history(entry_id: UUID, user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        row = (
            session.query(ReadingHistory)
            .filter(ReadingHistory.id == entry_id, ReadingHistory.user_id == user.id)  # only mine (ADR-048)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="history entry not found")
        session.delete(row)
        session.flush()
    return {"id": str(entry_id), "deleted": True}


@app.patch("/history/{entry_id}")
def update_history(
    entry_id: UUID,
    req: HistoryUpdate,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    fields = req.model_dump(exclude_unset=True)  # only what the client actually sent
    if "date_completed" in fields and fields["date_completed"] is None:
        raise HTTPException(status_code=422, detail="date_completed cannot be null")
    with db_manager.get_session() as session:
        row = (
            session.query(ReadingHistory)
            .filter(ReadingHistory.id == entry_id, ReadingHistory.user_id == user.id)
            .options(*_history_options())
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="history entry not found")
        if "date_completed" in fields:
            row.date_completed = fields["date_completed"]
        if "rating" in fields:
            row.user_rating = fields["rating"]
        if "notes" in fields:
            row.user_notes = fields["notes"]
        session.flush()
        return _history_item(row)


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


# ---------------------------------------------------------------------------
# SPA static serving (Lift 2 Stage 4) — served same-origin from this container.
# Registered LAST so every API route above takes precedence over the catch-all.
# ---------------------------------------------------------------------------


def _spa_dir() -> str:
    return os.environ.get("SPA_DIST_DIR", "/app/static")


def _spa_index() -> FileResponse:
    return FileResponse(os.path.join(_spa_dir(), "index.html"))


@app.get("/")
def spa_root():
    return _spa_index()


@app.get("/{full_path:path}")
def spa_catch_all(full_path: str):
    """Serve a real built file when one exists; otherwise return the SPA shell so
    client-side routes (e.g. /add, /history) resolve. A genuinely-missing asset (e.g. a
    bad /assets/* path) therefore also returns the shell (200), not a 404 — standard SPA
    catch-all behavior. The realpath check is a path-traversal guard: a candidate that
    escapes the dist dir falls back to the shell."""
    root = os.path.realpath(_spa_dir())
    candidate = os.path.realpath(os.path.join(root, full_path))
    if (candidate == root or candidate.startswith(root + os.sep)) and os.path.isfile(candidate):
        return FileResponse(candidate)
    return _spa_index()
