import os
import threading
import time
from functools import lru_cache

from google import genai
from google.genai import types

# Match the pgvector column dimension (Vector(1536) on Style/Trope). gemini-embedding-001
# defaults to 3072, so we must request 1536 explicitly.
EMBEDDING_DIMENSIONS = 1536

# Optional client-side pacing for the embedding endpoint, controlled by EMBED_MIN_INTERVAL (seconds
# between cache-MISS embed calls; 0/unset = no throttle). The interactive recommendation path leaves
# it unset, so live lookups pay zero added latency; the Flow-1 ETL sets a small value so a chunk's
# embedding burst during persist stays under the Gemini embedding RPM instead of 429-ing mid-run
# (the SDK retry in llm_retry.py can't outlast a sustained over-rate burst). lru_cache hits skip this.
_EMBED_MIN_INTERVAL = float(os.environ.get("EMBED_MIN_INTERVAL", "0") or "0")
_embed_lock = threading.Lock()
_last_embed = 0.0


def _throttle_embedding() -> None:
    if _EMBED_MIN_INTERVAL <= 0:
        return
    global _last_embed
    with _embed_lock:
        wait = _EMBED_MIN_INTERVAL - (time.monotonic() - _last_embed)
        if wait > 0:
            time.sleep(wait)
        _last_embed = time.monotonic()


@lru_cache(maxsize=128)
def get_cached_embedding(client: genai.Client, model_name: str, text: str) -> list[float]:
    """Shared helper to safely cache embeddings without leaking self. task_type
    SEMANTIC_SIMILARITY keeps stored vectors and query vectors in one representation space:
    this is the single embed chokepoint for both ETL ingestion and the recommendation flow,
    so both sides match. Changing task_type invalidates previously-stored vectors."""
    _throttle_embedding()
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
