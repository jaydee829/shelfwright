"""The ONE real-vs-fallback trope predicate (GH #111, semantics from PR #69).

A genre/mood "fallback" trope is one whose CLEANED name is (a subset of) the work's own
cleaned genres+moods — the two-phase fast pass re-encoded a genre/mood as a trope. The
justification column is deliberately NEVER consulted: many real scout tropes carry NULL
justification (semantic-collapse "attractor" tropes), so it conflates real with fallback —
the exact mistake the #65 prune nearly made (bugs.md 2026-06-24, memory
verify-backfill-distinguisher). Shared by the persist-time guard, clean_catalog's prune,
and (PR-D) the enrichment-reconciliation sweep, so the definitions can't diverge again."""

from __future__ import annotations

from agentic_librarian.etl.tag_cleaning import clean_trope_name


def is_fallback_trope_name(name: str, genres: list[str] | None, moods: list[str] | None) -> bool | None:
    """True = fallback (re-encoded genre/mood); False = genuine narrative trope;
    None = junk (cleans to nothing — neither real nor fallback)."""
    gm = {s.lower() for s in (set(genres or []) | set(moods or []))}
    cleaned = clean_trope_name(name)
    if not cleaned:
        return None
    return {c.lower() for c in cleaned} <= gm
