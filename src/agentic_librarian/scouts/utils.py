from functools import lru_cache

from google import genai


@lru_cache(maxsize=128)
def get_cached_embedding(client: genai.Client, model_name: str, text: str) -> list[float]:
    """Shared helper to safely cache embeddings without leaking 'self'."""
    response = client.models.embed_content(model=model_name, contents=text)
    return response.embeddings[0].values
