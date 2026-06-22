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


def _clean_one(tag: str, *, alias: dict[str, str], combo: dict[str, list[str]], denylist: set[str]) -> list[str]:
    n = _normalize(_bisac_reduce(_strip_uuid(tag)))
    if not n:
        return []
    if n in combo:
        return list(combo[n])  # already canonical
    if n in alias:
        return [alias[n]]
    if n in denylist or _HAS_DIGIT_RE.search(n) or len(n) <= 1:
        return []
    return [_titlecase(n)]  # unknown-but-valid: keep, cleaned


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        k = it.lower()
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def clean_genres(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    for tag in raw or []:
        out.extend(_clean_one(tag, alias=tag_maps.ALIAS_MAP, combo=tag_maps.COMBO_MAP, denylist=tag_maps.DENYLIST))
    result = _dedup(out)
    if len(result) > 1:  # drop over-broad umbrellas only when more specific genres remain
        pruned = [g for g in result if g not in tag_maps.CONDITIONAL_DROP]
        result = pruned or result  # never empty the list, even if every genre were an umbrella term
    return result


def clean_moods(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    for tag in raw or []:
        out.extend(_clean_one(tag, alias=tag_maps.MOOD_ALIAS_MAP, combo={}, denylist=tag_maps.MOOD_DENYLIST))
    return _dedup(out)
