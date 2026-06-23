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
    "sciencefiction": "Science Fiction",
    "sciense fiction": "Science Fiction",
    "action adventure": "Action & Adventure",
    "business economics": "Business & Economics",
    "business & economics": "Business & Economics",
    "epic": "Epic",
    "fantasy": "Fantasy",
    "lgbtq": "LGBTQ",
    "lgbt": "LGBTQ",
    "queer": "LGBTQ",
    "gay": "LGBTQ",
    "lesbian": "LGBTQ",
    "literary fiction": "Literary",
    "literature fiction": "Literary",
    "literary": "Literary",
    "literature": "Literary",
    "thriller suspense": "Thriller",
    "suspense": "Thriller",
    "fantasy fiction": "Fantasy",
    "young adult fiction": "Young Adult",
    "teen young adult": "Young Adult",
    "historical fiction": "Historical",
    "humour": "Humor",
    "humorous": "Humor",
    "comedy humor": "Humor",
    "aventure": "Adventure",
    "mystere": "Mystery",
    "guerre": "War",
    "classique": "Classics",
    "adulte": "Adult",
    "policier": "Crime",
    "thrillers": "Thriller",
}

# normalized true-combo slug -> list of canonical genres (split)
COMBO_MAP: dict[str, list[str]] = {
    "science fiction fantasy": ["Science Fiction", "Fantasy"],
    "fiction sci fi fantasy": ["Science Fiction", "Fantasy"],
    "science fiction et fantasy": ["Science Fiction", "Fantasy"],
    "thriller suspense science fiction fantasy": ["Thriller", "Science Fiction", "Fantasy"],
    "literature fiction science fiction fantasy": ["Literary", "Science Fiction", "Fantasy"],
    "literature fiction mystery": ["Literary", "Mystery"],
    "fiction fantasy epic": ["Fantasy", "Epic"],
    "fiction fantasy general": ["Fantasy"],
    "fiction fantasy action adventure": ["Fantasy", "Action & Adventure"],
    "fiction fantasy urban": ["Fantasy"],
    "fiction action adventure": ["Action & Adventure"],
    "fantasy young adult": ["Fantasy", "Young Adult"],
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
    "downloadable e books",
    "etc",
    "novels",
    "novella",
    "genre fiction",
    "movie",
    "geary",
    "poirot",
    "christopher",
    "dresden",
}

# canonical genres dropped iff other genres remain in the list
CONDITIONAL_DROP: set[str] = {"Fiction"}

# moods: permissive QC — collapse only, drop only clear junk
MOOD_ALIAS_MAP: dict[str, str] = {
    "lighthearted": "Lighthearted",
    "light hearted": "Lighthearted",
}
MOOD_DENYLIST: set[str] = {"audiobook", "ebook", "general", "n a", "na", "series cradle"}
