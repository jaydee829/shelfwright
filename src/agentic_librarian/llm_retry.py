"""Shared transient-error retry config for every Gemini call (mesh agents, enrichment scouts, and
embeddings). Gemini 2.5 capacity is being squeezed (503 "model experiencing high demand"); retrying
transient 5xx / 429 with exponential backoff lets a single call ride through a demand spike instead
of crashing the whole recommendation run (REC-020).

google-genai exposes this as `HttpRetryOptions`. It attaches two ways:
  * direct genai clients (scouts / embeddings): `genai.Client(http_options=genai_http_options())`
  * ADK agents: `Gemini(model=..., retry_options=RETRY_OPTIONS)` (ADK's Gemini model field)
"""

from __future__ import annotations

from google.genai import types

# Retry transient HTTP failures with exponential backoff: 429 (rate limit) + 5xx (server/overload).
RETRY_OPTIONS = types.HttpRetryOptions(
    attempts=5,
    initial_delay=1.0,
    max_delay=30.0,
    exp_base=2.0,
    http_status_codes=[429, 500, 502, 503, 504],
)


def genai_http_options() -> types.HttpOptions:
    """HttpOptions carrying the shared retry config, for `genai.Client(http_options=...)`."""
    return types.HttpOptions(retry_options=RETRY_OPTIONS)
