import os

from google import genai
from sqlalchemy.orm import Session

from agentic_librarian.db.models import Trope
from agentic_librarian.llm_retry import genai_http_options
from agentic_librarian.scouts.utils import get_cached_embedding


class TropeManager:
    """Manages trope seeding, vectorization, and similarity deduplication."""

    def __init__(self, session: Session, api_key: str = None):
        self.session = session
        self._api_key = api_key or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not self._api_key:
            raise ValueError("Google API key not set for TropeManager.")
        self.client = genai.Client(api_key=self._api_key, http_options=genai_http_options())
        self.model_name = "gemini-embedding-001"  # Current GA Gemini embedding model

    def _get_embedding(self, text: str) -> list[float]:
        """Fetch embedding from Gemini. Uses shared module-level cache."""
        return get_cached_embedding(self.client, self.model_name, text)

    def find_similar_trope(self, embedding: list[float], threshold: float = 0.85) -> Trope | None:
        """Find an existing trope with cosine similarity above threshold using SQL-level search."""
        max_distance = 1.0 - threshold

        similar_trope = (
            self.session.query(Trope)
            .filter(Trope.embedding.cosine_distance(embedding) <= max_distance)
            .order_by(Trope.embedding.cosine_distance(embedding))
            .first()
        )

        return similar_trope

    def standardize_trope(self, raw_tag: str, threshold: float = 0.85, description: str = None) -> Trope:
        """
        Maps a raw tag to a standardized Trope.
        Checks for exact name match first, then semantic similarity.
        Creates a new Trope if no match is found.
        """
        # 1. Exact Name Match
        existing = self.session.query(Trope).filter(Trope.name == raw_tag).first()
        if existing:
            if description and not existing.description:
                existing.description = description
            return existing

        # 2. Semantic Match
        embedding = self._get_embedding(raw_tag)
        similar = self.find_similar_trope(embedding, threshold=threshold)

        if similar:
            if description and not similar.description:
                similar.description = description
            return similar

        # 3. Create New
        new_trope = Trope(name=raw_tag, embedding=embedding, description=description)
        self.session.add(new_trope)
        self.session.flush()  # Ensure ID is populated for the caller
        # We don't commit here, let the caller handle it or use a flush
        return new_trope
