"""Analysis surface (Lift 2 Stage 2). Four beta views over the user's reading history:
a reading snapshot, genre & mood mix, top tropes (the signature fingerprint), and
authors & narrators. One endpoint returns all four — at beta scale a single round trip
beats four. Aggregation is done in Python over the user's rows (small data); the
embedding-based trope fingerprint and ratings-over-time are future work. Identity comes
from the auth context; rows are filtered by user.id (ADR-048)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import joinedload

from agentic_librarian.api import analysis_style
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.db.models import (
    Author,
    AuthorStyle,
    Edition,
    ReadingHistory,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager

router = APIRouter()
db_manager = DatabaseManager()

_TOP_N = 10


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (for tests) — the mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


def _ranked(counter: Counter) -> list[dict]:
    return [{"name": name, "count": count} for name, count in counter.most_common(_TOP_N)]


@router.get("/analysis")
def get_analysis(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        rows = (
            session.query(ReadingHistory)
            .filter(ReadingHistory.user_id == user.id)  # my reading, not the commons (ADR-048)
            .options(
                # Flat chained loaders (repo convention — see recommendations.py / main.py).
                # The shared edition->work spine is merged by SQLAlchemy; collections use
                # selectinload to avoid joined-collection row inflation.
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .selectinload(Work.contributors)
                .joinedload(WorkContributor.author),
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .selectinload(Work.tropes)
                .joinedload(WorkTrope.trope),
                joinedload(ReadingHistory.edition).selectinload(Edition.narrators),
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .selectinload(Work.contributors)
                .joinedload(WorkContributor.author)
                .selectinload(Author.styles)
                .joinedload(AuthorStyle.style),
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .selectinload(Work.styles)
                .joinedload(WorkStyle.style),
            )
            .all()
        )

        this_year = datetime.now(UTC).year
        ratings = [r.user_rating for r in rows if r.user_rating is not None]
        formats: Counter = Counter()
        genres: Counter = Counter()
        moods: Counter = Counter()
        tropes: Counter = Counter()
        authors: Counter = Counter()
        narrators: Counter = Counter()
        author_names: set[str] = set()
        style_maps: list[dict] = []

        for r in rows:
            edition = r.edition
            work = edition.work
            if edition.format:
                formats[edition.format] += 1
            for g in work.genres or []:
                genres[g] += 1
            for m in work.moods or []:
                moods[m] += 1
            for wt in work.tropes:
                tropes[wt.trope.name] += 1
            for c in work.contributors:
                if c.role == "Author":
                    authors[c.author.name] += 1
                    author_names.add(c.author.name)
            for narrator in edition.narrators:
                narrators[narrator.name] += 1
            baseline: dict = {}
            for c in work.contributors:
                if c.role == "Author":
                    for asty in c.author.styles:
                        baseline[asty.attribute_type] = asty.style
            overrides = {wsty.attribute_type: wsty.style for wsty in work.styles}
            style_maps.append({**baseline, **overrides})

        embed = analysis_style.default_embedder()
        style_radar = analysis_style.aggregate_radar(style_maps, embed)
        style_cloud = analysis_style.aggregate_cloud(style_maps)

        return {
            "snapshot": {
                "total_read": len(rows),
                "read_this_year": sum(1 for r in rows if r.date_completed and r.date_completed.year == this_year),
                "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
                "distinct_authors": len(author_names),
                "formats": _ranked(formats),
            },
            "genres": _ranked(genres),
            "moods": _ranked(moods),
            "top_tropes": _ranked(tropes),
            "authors": _ranked(authors),
            "narrators": _ranked(narrators),
            "style_radar": style_radar,
            "style_cloud": style_cloud,
        }
