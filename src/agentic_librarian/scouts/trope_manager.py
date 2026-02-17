import os

import numpy as np
from agentic_librarian.db.models import Trope
from google import genai
from sqlalchemy.orm import Session


class TropeManager:
    """Manages trope seeding, vectorization, and similarity deduplication."""

    def __init__(self, session: Session, api_key: str = None):
        self.session = session
        self._api_key = api_key or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not self._api_key:
            raise ValueError("Google API key not set for TropeManager.")
        self.client = genai.Client(api_key=self._api_key)
        self.model_name = "text-embedding-004"  # Standard Gemini embedding model

    def _get_embedding(self, text: str) -> list[float]:
        """Fetch embedding from Gemini."""
        # Use the newer google-genai SDK if available, or fallback
        response = self.client.models.embed_content(model=self.model_name, contents=text)
        return response.embeddings[0].values

    def find_similar_trope(self, embedding: list[float], threshold: float = 0.85) -> Trope | None:
        """Find an existing trope with cosine similarity above threshold."""
        existing_tropes = self.session.query(Trope).all()
        if not existing_tropes:
            return None

        # Convert to numpy for fast calculation
        target = np.array(embedding)

        best_match = None
        highest_sim = -1.0

        for trope in existing_tropes:
            if trope.embedding is None:
                continue

            # Simple cosine similarity: (A . B) / (||A|| * ||B||)
            # Gemini embeddings are often normalized, but let's be safe
            existing = np.array(trope.embedding)
            similarity = np.dot(target, existing) / (np.linalg.norm(target) * np.linalg.norm(existing))

            if similarity > highest_sim:
                highest_sim = similarity
                best_match = trope

        if highest_sim >= threshold:
            return best_match

        return None

    def standardize_trope(self, raw_tag: str, threshold: float = 0.85) -> Trope:
        """
        Maps a raw tag to a standardized Trope.
        Checks for exact name match first, then semantic similarity.
        Creates a new Trope if no match is found.
        """
        # 1. Exact Name Match
        existing = self.session.query(Trope).filter(Trope.name == raw_tag).first()
        if existing:
            return existing

        # 2. Semantic Match
        embedding = self._get_embedding(raw_tag)
        similar = self.find_similar_trope(embedding, threshold=threshold)

        if similar:
            return similar

        # 3. Create New
        new_trope = Trope(name=raw_tag, embedding=embedding)
        self.session.add(new_trope)
        self.session.flush()  # Ensure ID is populated for the caller
        # We don't commit here, let the caller handle it or use a flush
        return new_trope
