from datetime import date

import numpy as np
from agentic_librarian.db.models import Author, Edition, ReadingHistory, Suggestions, Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.scouts.trope_manager import TropeManager
from sqlalchemy import func, select

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("agentic_librarian")

# Initialize DatabaseManager (ADR-006)
db_manager = DatabaseManager()


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
def search_internal_database(target_tropes: list[str], limit: int = 10) -> list[dict]:
    """
    Performs a pgvector similarity search across the internal database.

    Args:
        target_tropes: List of trope names to search for.
        limit: Max number of results to return.
    """
    with db_manager.get_session() as session:
        tm = TropeManager(session=session)

        # 1. Vectorize concepts
        embeddings = [tm._get_embedding(t) for t in target_tropes]
        avg_vector = np.mean(embeddings, axis=0).tolist()

        # 2. Optimized search: Find similar tropes first
        similar_tropes = session.query(Trope).order_by(Trope.embedding.cosine_distance(avg_vector)).limit(10).all()
        trope_ids = [t.id for t in similar_tropes]

        # 3. Find Works linked to these tropes
        works = (
            session.query(Work).join(WorkTrope).filter(WorkTrope.trope_id.in_(trope_ids)).distinct().limit(limit).all()
        )

        return [
            {
                "id": str(w.id),
                "title": w.title,
                "authors": [a.name for a in w.authors],
                "genres": w.genres,
                "description": w.description,
            }
            for w in works
        ]


@mcp.tool()
def get_unacted_suggestions(target_tropes: list[str], limit: int = 5) -> list[dict]:
    """Pulls previous recommendations that were never read or ignored."""
    with db_manager.get_session() as session:
        # Find suggestions with status 'Suggested'
        suggestions = session.query(Suggestions).filter(Suggestions.status == "Suggested").join(Work).limit(limit).all()

        return [
            {
                "id": str(s.work.id),
                "title": s.work.title,
                "justification": s.justification,
                "suggested_at": s.suggested_at.isoformat() if s.suggested_at else None,
            }
            for s in suggestions
        ]


@mcp.tool()
def check_reading_history(title: str, author: str) -> dict:
    """Checks if a book has been read."""
    with db_manager.get_session() as session:
        entry = (
            session.query(ReadingHistory)
            .join(Edition)
            .join(Work)
            .join(Work.authors)
            .filter(Work.title == title)
            .filter(Author.name == author)
            .first()
        )

        if entry:
            return {"status": "Read", "date_completed": entry.date_completed.isoformat(), "rating": entry.user_rating}
        return {"status": "Unread"}


@mcp.tool()
def update_reading_status(title: str, author: str, status: str, notes: str = None) -> str:
    """Updates history based on feedback (e.g. 'I read that years ago')."""
    try:
        with db_manager.get_session() as session:
            # Find the work/edition first
            work = session.query(Work).join(Work.authors).filter(Work.title == title, Author.name == author).first()
            if not work:
                return f"Work '{title}' by {author} not found in database."

            # Find first edition
            edition = session.query(Edition).filter_by(work_id=work.id).first()
            if not edition:
                # Create a placeholder edition if none exists
                edition = Edition(work=work, format="Unknown")
                session.add(edition)
                session.flush()

            if status.lower() == "read":
                history = ReadingHistory(
                    edition=edition,
                    date_completed=date.today(),  # Placeholder for manual addition
                    user_notes=notes,
                )
                session.add(history)

            session.flush()  # ADR-016
            return f"Successfully updated status for '{title}' to {status}."
    except Exception as e:
        return f"Error updating status: {str(e)}"


@mcp.tool()
def log_suggestion(work_id: str, context: str, justification: str, conversation_id: str = None) -> str:
    """Logs a new recommendation to the Suggestions table."""
    try:
        with db_manager.get_session() as session:
            suggestion = Suggestions(
                work_id=work_id,
                context=context,
                justification=justification,
                conversation_id=conversation_id,
                status="Suggested",
            )
            session.add(suggestion)
            session.flush()
            return f"Logged suggestion for work {work_id}."
    except Exception as e:
        return f"Error logging suggestion: {str(e)}"


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
    """Returns metadata and tropes for a work."""
    with db_manager.get_session() as session:
        work = session.query(Work).filter_by(id=work_id).first()
        if not work:
            return {}

        tropes = [{"name": wt.trope.name, "description": wt.trope.description} for wt in work.tropes]

        return {"title": work.title, "description": work.description, "genres": work.genres, "tropes": tropes}


if __name__ == "__main__":
    mcp.run()
