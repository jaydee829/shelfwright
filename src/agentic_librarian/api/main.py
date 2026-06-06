from agentic_librarian.db.models import Edition, ReadingHistory, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import joinedload

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
