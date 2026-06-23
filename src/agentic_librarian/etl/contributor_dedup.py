"""Dedup Author/Narrator rows that differ only by case/whitespace, folding all links onto one
survivor (Spec 2026-06-23). Session in, summary out; the CLI is scripts/clean_catalog.py. Sibling
of etl/tag_backfill.py for the contributor tables."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


def norm_name(name: str | None) -> str:
    """Whitespace-collapsed, case-folded key. Two rows are 'the same' iff these match."""
    return " ".join((name or "").split()).casefold()


def _pick_survivor(rows: list):
    """Best-cased name wins (any uppercase > all-lowercase); deterministic id tiebreak."""
    return sorted(rows, key=lambda r: (0 if any(c.isupper() for c in r.name) else 1, str(r.id)))[0]


def _dup_groups(rows: list) -> list[list]:
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[norm_name(r.name)].append(r)
    return [g for g in groups.values() if len(g) > 1]


@dataclass
class ContributorChange:
    kind: str  # "author" | "narrator"
    survivor: str
    merged: list[str]  # loser display names folded into the survivor
