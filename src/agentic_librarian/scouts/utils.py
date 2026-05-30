from functools import lru_cache

from google import genai
from google.genai import types

# Match the pgvector column dimension (Vector(1536) on Style/Trope). gemini-embedding-001
# defaults to 3072, so we must request 1536 explicitly.
EMBEDDING_DIMENSIONS = 1536


@lru_cache(maxsize=128)
def get_cached_embedding(client: genai.Client, model_name: str, text: str) -> list[float]:
    """Shared helper to safely cache embeddings without leaking 'self'."""
    response = client.models.embed_content(
        model=model_name,
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS),
    )
    return response.embeddings[0].values
