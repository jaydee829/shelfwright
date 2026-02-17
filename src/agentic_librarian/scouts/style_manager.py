import os

import numpy as np
from agentic_librarian.db.models import Style
from google import genai
from sqlalchemy.orm import Session


class StyleManager:
    """Manages style seeding, vectorization, and similarity deduplication for Authors and Narrators."""

    def __init__(self, session: Session, api_key: str = None):
        self.session = session
        self._api_key = api_key or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not self._api_key:
            raise ValueError("Google API key not set for StyleManager.")
        self.client = genai.Client(api_key=self._api_key)
        self.model_name = "text-embedding-004"

    def _get_embedding(self, text: str) -> list[float]:
        """Fetch embedding from Gemini."""
        response = self.client.models.embed_content(model=self.model_name, contents=text)
        return response.embeddings[0].values

    def find_similar_style(self, embedding: list[float], category: str, threshold: float = 0.85) -> Style | None:
        """Find an existing style in the same category with cosine similarity above threshold."""
        existing_styles = self.session.query(Style).filter(Style.category == category).all()
        if not existing_styles:
            return None

        target = np.array(embedding)
        best_match = None
        highest_sim = -1.0

        for style in existing_styles:
            if style.embedding is None:
                continue

            existing = np.array(style.embedding)
            similarity = np.dot(target, existing) / (np.linalg.norm(target) * np.linalg.norm(existing))

            if similarity > highest_sim:
                highest_sim = similarity
                best_match = style

        if highest_sim >= threshold:
            return best_match

        return None

    def standardize_style(self, raw_tag: str, category: str, threshold: float = 0.85) -> Style:
        """
        Maps a raw style tag to a standardized Style entity.
        Checks for exact name match first, then semantic similarity within the category.
        """
        # 1. Exact Name Match
        existing = self.session.query(Style).filter(Style.name == raw_tag, Style.category == category).first()
        if existing:
            return existing

        # 2. Semantic Match
        embedding = self._get_embedding(raw_tag)
        similar = self.find_similar_style(embedding, category=category, threshold=threshold)

        if similar:
            return similar

        # 3. Create New
        new_style = Style(name=raw_tag, category=category, embedding=embedding)
        self.session.add(new_style)
        self.session.flush()
        return new_style
