from __future__ import annotations

from datetime import date

import numpy as np
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
from agentic_librarian.scouts.style_manager import StyleManager
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
def search_internal_database(target_tropes: list[str], target_styles: list[str] = None, limit: int = 10) -> list[dict]:
    """
    Performs a pgvector similarity search across tropes and literary styles.

    Args:
        target_tropes: List of trope names to search for.
        target_styles: Optional list of style attributes (e.g. 'fast-paced', 'grimdark').
        limit: Max number of results to return.
    """
    with db_manager.get_session() as session:
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)

        candidate_work_ids = set()

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

        # 3. Final Work Retrieval
        if not candidate_work_ids:
            return []

        works = session.query(Work).filter(Work.id.in_(list(candidate_work_ids))).limit(limit).all()

        return [
            {
                "id": str(w.id),
                "title": w.title,
                "authors": [c.author.name for c in w.contributors],
                "genres": w.genres,
                "description": w.description,
            }
            for w in works
        ]


@mcp.tool()
def get_unacted_suggestions(target_tropes: list[str], target_styles: list[str] = None, limit: int = 5) -> list[dict]:
    """
    Pulls previous recommendations that were never read or ignored,
    ranked by similarity to current target vibes.
    """
    with db_manager.get_session() as session:
        # 1. Get all unacted suggestions
        query = session.query(Suggestions).filter(Suggestions.status == "Suggested").join(Work)
        suggestions = query.all()

        if not suggestions:
            return []

        # 2. Rank them semantically if targets are provided
        # (For simplicity in this tool, we'll return them all if no targets,
        # or perform a basic filter/rank if they are)
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
                work_trope_vecs = [np.array(wt.trope.embedding) for wt in s.work.tropes if wt.trope.embedding]
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

                work_style_vecs = [np.array(sl.style.embedding) for sl in style_links if sl.style.embedding]
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


@mcp.tool()
def update_reading_status(title: str, author: str, status: str, notes: str | None = None) -> str:
    """Updates history based on feedback (e.g. 'I read that years ago')."""
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
def log_suggestion(work_id: str, context: str, justification: str, conversation_id: str | None = None) -> str:
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
def update_suggestion_status(work_id: str, status: str) -> str:
    """
    Updates the status of a suggestion (e.g. 'Accepted', 'Dismissed', 'Already Read').
    This ensures unacted suggestions are cleaned up based on feedback.
    """
    try:
        with db_manager.get_session() as session:
            suggestion = (
                session.query(Suggestions)
                .filter_by(work_id=work_id, status="Suggested")
                .order_by(Suggestions.suggested_at.desc())
                .first()
            )
            if not suggestion:
                return f"No active suggestion found for work {work_id}."

            suggestion.status = status
            session.flush()
            return f"Updated suggestion for work {work_id} to status: {status}."
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
    with db_manager.get_session() as session:
        work = session.query(Work).filter_by(id=work_id).first()
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


if __name__ == "__main__":
    mcp.run()
