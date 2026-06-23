"""Pure QC/cleaning for Work.genres / Work.moods (Spec 2026-06-22). No I/O; deterministic;
parametrised by the curated maps in tag_maps.py."""

from __future__ import annotations

import re

from agentic_librarian.etl import tag_maps

_UUID_RE = re.compile(r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_HAS_DIGIT_RE = re.compile(r"\d")
_BISAC_FILLER = {"general", "fiction", "nonfiction", "non fiction", "books", "miscellaneous"}
_JUNK_SUBSTRINGS = ("fictitious character", "imaginary place", "motion picture")
# Precomputed once at import (clean_trope_name runs in a hot ETL loop): union of the genre + mood maps.
_TROPE_ALIAS = {**tag_maps.ALIAS_MAP, **tag_maps.MOOD_ALIAS_MAP}
_TROPE_DENYLIST = tag_maps.DENYLIST | tag_maps.MOOD_DENYLIST


def _strip_uuid(tag: object) -> str:
    if not isinstance(tag, str):
        return ""  # non-string tags (scraper noise) are dropped downstream
    return _UUID_RE.sub("", tag)


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


def _clean_one(
    tag: str, *, alias: dict[str, str], combo: dict[str, list[str]], denylist: set[str], _depth: int = 0
) -> list[str]:
    n = _normalize(_bisac_reduce(_strip_uuid(tag)))
    if not n:
        return []
    if n in combo:
        return list(combo[n])  # explicit combo wins (e.g. multi-token leaves)
    if n in alias:
        return [alias[n]]
    if n in denylist or _HAS_DIGIT_RE.search(n) or len(n) <= 1:
        return []
    if any(j in n for j in _JUNK_SUBSTRINGS):  # entity/tie-in noise, e.g. "* fictitious character"
        return []
    if n.startswith("fiction ") and _depth < 4:
        # BISAC "fiction" umbrella: drop it, split the leaf on the first token, canonicalise each
        # piece recursively (so e.g. "fiction fantasy military" -> [Fantasy, Military]).
        first, _, tail = n[len("fiction ") :].partition(" ")
        out = _clean_one(first, alias=alias, combo=combo, denylist=denylist, _depth=_depth + 1)
        if tail:
            out += _clean_one(tail, alias=alias, combo=combo, denylist=denylist, _depth=_depth + 1)
        return out
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


def clean_trope_name(name: str) -> list[str]:
    """Clean one Trope.name. The table mixes genuine narrative tropes (free text from the LLM, e.g.
    'The Chosen One (Subverted)', 'Mirror / Shadow Self') with genre/mood-slug fallbacks
    ('science-fiction-fantasy-<uuid>', 'literary-fiction', 'tense'). The two are told apart by
    casing: a real trope always has an uppercase letter; the slugs are all-lowercase.

    - KNOWN genre/mood tag (combo/alias match after normalising) -> split / canonicalise, regardless
      of casing (so 'science-fiction-fantasy-<uuid>' -> ['Science Fiction', 'Fantasy']).
    - Has an uppercase letter -> a genuine trope: keep it VERBATIM (only the UUID tail stripped), so
      '/', 'vs.', '(...)', em-dashes and original capitalisation all survive intact.
    - Otherwise a bare lowercase slug -> drop if junk/numeric/denylisted/entity-noise, else title-case.

    Returns 0..N names, de-duped."""
    if not isinstance(name, str):
        return []
    stripped = _strip_uuid(name).strip()
    norm = _normalize(stripped)
    if not norm:
        return []
    if norm in tag_maps.COMBO_MAP:
        return _dedup(list(tag_maps.COMBO_MAP[norm]))
    if norm in _TROPE_ALIAS:
        return [_TROPE_ALIAS[norm]]
    if any(c.isupper() for c in stripped):  # genuine free-text trope -> preserve verbatim
        return [stripped]
    if norm in _TROPE_DENYLIST or _HAS_DIGIT_RE.search(norm) or len(norm) <= 1:
        return []
    if any(j in norm for j in _JUNK_SUBSTRINGS):  # entity/tie-in noise
        return []
    return [_titlecase(norm)]  # bare lowercase slug
