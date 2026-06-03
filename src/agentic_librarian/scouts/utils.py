from functools import lru_cache

from google import genai
from google.genai import types

# Match the pgvector column dimension (Vector(1536) on Style/Trope). gemini-embedding-001
# defaults to 3072, so we must request 1536 explicitly.
EMBEDDING_DIMENSIONS = 1536


@lru_cache(maxsize=128)
def get_cached_embedding(client: genai.Client, model_name: str, text: str) -> list[float]:
    """Shared helper to safely cache embeddings without leaking 'self'. task_type
    SEMANTIC_SIMILARITY keeps stored vectors and query vectors in one representation space:
    this is the single embed chokepoint for both ETL ingestion and the recommendation flow,
    so both sides match. Changing task_type invalidates previously-stored vectors."""
    response = client.models.embed_content(
        model=model_name,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="SEMANTIC_SIMILARITY",
            output_dimensionality=EMBEDDING_DIMENSIONS,
        ),
    )
    if not response or not response.embeddings:
        raise ValueError(f"Embedding generation returned no result for text: {text!r}")
    return response.embeddings[0].values
