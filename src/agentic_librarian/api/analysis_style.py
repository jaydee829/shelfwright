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

import logging
import math
import os
import threading
from collections import Counter
from collections.abc import Callable
from typing import Protocol

from agentic_librarian.scouts.utils import get_cached_embedding

logger = logging.getLogger(__name__)

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
    # NOTE: deliberate inversion — the scout attribute is "emotional_distance"
    # (high = cold/detached) but the axis is "warmth" (high = intimate/warm). The
    # warmth anchors below are warmth-oriented, so an "intimate" style scores ~1.
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
# Guarded by _lock so concurrent requests don't double-embed or read a half-filled cache.
_anchor_cache: dict[str, tuple[list[float], list[float]]] = {}
_lock = threading.Lock()


def get_anchor_vectors(embed: Callable[[str], list[float]]) -> dict[str, tuple[list[float], list[float]]]:
    """Embed each axis's anchor pair once and memoize (thread-safe, double-checked).

    The fully-built dict is published with a single atomic rebind, so a concurrent
    reader sees the cache as either empty or complete — never partially filled (which
    would KeyError on a missing axis). An embedding failure leaves the cache empty.
    """
    global _anchor_cache
    if not _anchor_cache:
        with _lock:
            if not _anchor_cache:
                built = {axis: (embed(low), embed(high)) for axis, (low, high) in ANCHORS.items()}
                _anchor_cache = built
    return _anchor_cache


def default_embedder() -> Callable[[str], list[float]] | None:
    """Real embedder using the same model/space as the stored Style vectors, or
    None when no API key is configured (radar then degrades to all-null)."""
    if not os.environ.get("GOOGLE_SEARCH_API_KEY"):
        return None
    return lambda text: get_cached_embedding(_EMBED_MODEL, text)


def aggregate_radar(
    style_maps: list[dict[str, _StyleLike]],
    embed: Callable[[str], list[float]] | None,
) -> dict[str, float | None]:
    """Mean 0-1 score per axis across the user's read works. None when an axis has
    no scorable data, no embedder, or the anchor embedding can't be fetched.

    Never raises: an embedding failure (bad/absent key, quota, network) degrades the
    whole radar to all-null so /analysis stays up — the radar just hides client-side.
    """
    scores: dict[str, list[float]] = {axis: [] for axis in AXES}
    # Gather scorable (axis, vector) pairs first so we only pay for anchor embeddings
    # when the shelf actually has ordinal styles to place.
    scorable = [
        (RADAR_ATTR_TO_AXIS[attr], style.embedding)
        for style_map in style_maps
        for attr, style in style_map.items()
        if attr in RADAR_ATTR_TO_AXIS and style is not None and style.embedding is not None
    ]
    if embed is not None and scorable:
        try:
            anchors = get_anchor_vectors(embed)
        except Exception:  # noqa: BLE001 — any embed failure must degrade, not 500
            logger.warning("style radar: anchor embedding failed; degrading to null", exc_info=True)
            anchors = None
        if anchors is not None:
            for axis, vec in scorable:
                low, high = anchors[axis]
                s = score_axis(vec, low, high)
                if s is not None:
                    scores[axis].append(s)
    return {axis: (math.fsum(v) / len(v) if v else None) for axis, v in scores.items()}


def aggregate_cloud(style_maps: list[dict[str, _StyleLike]], top_n: int = 20) -> list[dict]:
    """Frequency of nominal style values across read works, title-cased so
    case/punctuation duplicates merge."""
    counter: Counter[str] = Counter()
    for style_map in style_maps:
        for attr, style in style_map.items():
            if attr in CLOUD_ATTRS and style is not None and style.name:
                counter[style.name.title()] += 1
    return [{"name": name, "count": count} for name, count in counter.most_common(top_n)]
