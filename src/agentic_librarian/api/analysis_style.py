"""Style scoring for the Analysis radar + style cloud (GH #13).

Bins categorical style values to a 0-1 magnitude per axis by projecting each
``Style.embedding`` onto a per-axis bipolar anchor pair (low phrase -> high
phrase). Anchors are embedded once in the same space as the stored style
vectors (gemini-embedding-001, 1536-d, SEMANTIC_SIMILARITY -- see
scouts/utils.get_cached_embedding) and cached. Pure vector math otherwise.

Nominal style attributes (tone, prose style, dialogue, perspective) have no
single axis; they feed the style word cloud instead.
"""

from __future__ import annotations

import math
import os
from collections import Counter
from collections.abc import Callable
from typing import Protocol

from agentic_librarian.scouts.utils import get_cached_embedding

# Fixed display order of the radar axes.
AXES: tuple[str, ...] = (
    "pace",
    "density",
    "depth",
    "inner_focus",
    "humor",
    "warmth",
    "lexicon",
    "world_building",
)

# attribute_type (from AuthorStyle/WorkStyle) -> radar axis.
RADAR_ATTR_TO_AXIS: dict[str, str] = {
    "pacing": "pace",
    "prose_density": "density",
    "thematic_depth": "depth",
    "interiority": "inner_focus",
    "humor": "humor",
    "emotional_distance": "warmth",
    "lexicon": "lexicon",
    "world_building": "world_building",
}

# Nominal attribute_types -> the style cloud (no axis).
CLOUD_ATTRS: frozenset[str] = frozenset({"tone", "style", "dialogue_style", "perspective"})

# Bipolar anchor phrases per axis: (low pole, high pole). High = the "more" end.
ANCHORS: dict[str, tuple[str, str]] = {
    "pace": ("slow-burn, languid, meandering pace", "breakneck, fast-paced, propulsive pace"),
    "density": ("spare, minimalist, sparse prose", "dense, ornate, flowery prose"),
    "depth": ("light, breezy entertainment", "heavy, philosophical, weighty themes"),
    "inner_focus": ("external, plot-driven, action-focused", "deeply introspective, interior character thoughts"),
    "humor": ("serious, humorless, grave", "constantly comedic, funny, humorous"),
    "warmth": ("clinical, detached, emotionally cold", "intimate, warm, emotionally close"),
    "lexicon": ("plain, simple, accessible vocabulary", "archaic, academic, specialized vocabulary"),
    "world_building": ("minimal, sparse world-building", "immersive, richly detailed world-building"),
}

_EMBED_MODEL = "gemini-embedding-001"  # must match StyleManager / stored Style vectors


def _dot(a: list[float], b: list[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b, strict=False))


def score_axis(value_vec: list[float], low_vec: list[float], high_vec: list[float]) -> float | None:
    """Project ``value_vec`` onto the low->high direction. low -> 0, high -> 1, clamped.

    Returns None if the anchors are degenerate (identical), which would make the
    axis undefined.
    """
    direction = [h - lo for h, lo in zip(high_vec, low_vec, strict=False)]
    denom = _dot(direction, direction)
    if denom == 0.0:
        return None
    offset = [v - lo for v, lo in zip(value_vec, low_vec, strict=False)]
    t = _dot(offset, direction) / denom
    return max(0.0, min(1.0, t))


class _StyleLike(Protocol):
    name: str
    embedding: list[float] | None


# Module-level anchor cache: axis -> (low_vec, high_vec). Filled once per process.
_anchor_cache: dict[str, tuple[list[float], list[float]]] = {}
_genai_client = None


def get_anchor_vectors(embed: Callable[[str], list[float]]) -> dict[str, tuple[list[float], list[float]]]:
    """Embed each axis's anchor pair once and memoize."""
    if not _anchor_cache:
        for axis, (low, high) in ANCHORS.items():
            _anchor_cache[axis] = (embed(low), embed(high))
    return _anchor_cache


def default_embedder() -> Callable[[str], list[float]] | None:
    """Real embedder using the same model/space as the stored Style vectors, or
    None when no API key is configured (radar then degrades to all-null)."""
    global _genai_client
    key = os.environ.get("GOOGLE_SEARCH_API_KEY")
    if not key:
        return None
    if _genai_client is None:
        from google import genai

        from agentic_librarian.llm_retry import genai_http_options

        _genai_client = genai.Client(api_key=key, http_options=genai_http_options())
    client = _genai_client
    return lambda text: get_cached_embedding(client, _EMBED_MODEL, text)


def aggregate_radar(
    style_maps: list[dict[str, _StyleLike]],
    embed: Callable[[str], list[float]] | None,
) -> dict[str, float | None]:
    """Mean 0-1 score per axis across the user's read works. None when an axis has
    no scorable data (or no embedder)."""
    scores: dict[str, list[float]] = {axis: [] for axis in AXES}
    if embed is not None:
        anchors = get_anchor_vectors(embed)
        for style_map in style_maps:
            for attr, style in style_map.items():
                axis = RADAR_ATTR_TO_AXIS.get(attr)
                if axis is None or style.embedding is None:
                    continue
                low, high = anchors[axis]
                s = score_axis(style.embedding, low, high)
                if s is not None:
                    scores[axis].append(s)
    return {axis: (math.fsum(v) / len(v) if v else None) for axis, v in scores.items()}


def aggregate_cloud(style_maps: list[dict[str, _StyleLike]], top_n: int = 20) -> list[dict]:
    """Frequency of nominal style values across read works, title-cased so
    case/punctuation duplicates merge."""
    counter: Counter[str] = Counter()
    for style_map in style_maps:
        for attr, style in style_map.items():
            if attr in CLOUD_ATTRS and style.name:
                counter[style.name.title()] += 1
    return [{"name": name, "count": count} for name, count in counter.most_common(top_n)]
