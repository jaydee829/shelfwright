"""Pure QC/cleaning for Work.genres / Work.moods (Spec 2026-06-22). No I/O; deterministic;
parametrised by the curated maps in tag_maps.py."""

from __future__ import annotations

import re

from agentic_librarian.etl import tag_maps

_UUID_RE = re.compile(r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_HAS_DIGIT_RE = re.compile(r"\d")
_BISAC_FILLER = {"general", "fiction", "nonfiction", "non fiction", "books", "miscellaneous"}


def _strip_uuid(tag: str) -> str:
    return _UUID_RE.sub("", tag or "")


def _normalize(tag: str) -> str:
    s = (tag or "").replace("-", " ").replace("_", " ")
    return " ".join(s.lower().split())


def _bisac_reduce(tag: str) -> str:
    """BISAC path 'A / B / C' -> the deepest non-filler segment; otherwise the tag unchanged."""
    if "/" not in tag:
        return tag
    segments = [seg.strip() for seg in tag.split("/") if seg.strip()]
    for seg in reversed(segments):
        if _normalize(seg) not in _BISAC_FILLER:
            return seg
    return segments[-1] if segments else ""


def _titlecase(norm: str) -> str:
    return " ".join(w.capitalize() for w in norm.split())
