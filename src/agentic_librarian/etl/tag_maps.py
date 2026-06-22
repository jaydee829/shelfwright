"""Curated lookup tables for genre/mood cleaning (Spec 2026-06-22). Keys are NORMALIZED
(lowercase, hyphens/underscores -> spaces, whitespace-collapsed); values are the canonical
display spelling, following BISAC where one exists. Seeded from the operator's known examples;
expand from the `scripts/clean_tags.py --inventory` output during rollout."""

from __future__ import annotations

# normalized variant -> canonical (BISAC-formatted) spelling
ALIAS_MAP: dict[str, str] = {
    "fiction": "Fiction",
    "nonfiction": "Nonfiction",
    "non fiction": "Nonfiction",
    "sci fi": "Science Fiction",
    "scifi": "Science Fiction",
    "sf": "Science Fiction",
    "science fiction": "Science Fiction",
    "action adventure": "Action & Adventure",
    "business economics": "Business & Economics",
    "business & economics": "Business & Economics",
    "epic": "Epic",
    "fantasy": "Fantasy",
}

# normalized true-combo slug -> list of canonical genres (split)
COMBO_MAP: dict[str, list[str]] = {
    "science fiction fantasy": ["Science Fiction", "Fantasy"],
}

# normalized tags dropped always (non-genres: formats, fillers)
DENYLIST: set[str] = {
    "audiobook",
    "audio",
    "audio cd",
    "audible audio",
    "ebook",
    "e book",
    "kindle edition",
    "paperback",
    "hardcover",
    "mass market paperback",
    "general",
    "books",
    "miscellaneous",
    "uncategorized",
    "other",
}

# canonical genres dropped iff other genres remain in the list
CONDITIONAL_DROP: set[str] = {"Fiction"}

# moods: permissive QC — collapse only, drop only clear junk
MOOD_ALIAS_MAP: dict[str, str] = {
    "lighthearted": "Lighthearted",
    "light hearted": "Lighthearted",
}
MOOD_DENYLIST: set[str] = {"audiobook", "ebook", "general", "n a", "na"}
