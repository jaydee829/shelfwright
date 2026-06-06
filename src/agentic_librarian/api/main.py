from agentic_librarian.db.models import (
    Edition,
    ReadingHistory,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import joinedload, selectinload

app = FastAPI(title="Agentic Librarian API")
db_manager = DatabaseManager()


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/db")
def db_health_check():
    try:
        with db_manager.get_session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "connected"}
    except Exception as e:
        # 503 so platform health probes and monitors see the failure (HTTP status,
        # not body, is what they key on). Detail is safe: the service is IAM-gated.
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


@app.get("/history")
def get_history():
    with db_manager.get_session() as session:
        # Query reading history with eager loading for efficiency
        history_entries = (
            session.query(ReadingHistory)
            .join(Edition)
            .join(Work)
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
                "authors": [c.author.name for c in h.edition.work.contributors if c.role == "Author"],  # ETL always writes role="Author"
                "date_completed": h.date_completed.isoformat() if h.date_completed else None,  # schema forbids NULL; guard is defensive only
                "rating": h.user_rating,
                "format": h.edition.format,
            }
            for h in history_entries
        ]


@app.get("/works")
def get_works(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
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
                "authors": [c.author.name for c in w.contributors if c.role == "Author"],  # ETL always writes role="Author"
                "publication_year": w.original_publication_year,
                "genres": w.genres or [],
                "moods": w.moods or [],
                "tropes": [wt.trope.name for wt in w.tropes],
                "styles": [{"attribute": ws.attribute_type, "name": ws.style.name} for ws in w.styles],
            }
            for w in works
        ]
