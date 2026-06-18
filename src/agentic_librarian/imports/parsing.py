"""Pure CSV parsing/normalization for bulk import (Spec 2026-06-18). No I/O — the highest
test-value surface, where Goodreads/generic format variability lives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Goodreads exports carry this stable header signature.
_GOODREADS_SIGNATURE = {"Book Id", "Title", "Author", "Exclusive Shelf"}

_GOODREADS_MAP = {
    "title": "Title",
    "author": "Author",
    "format": "Binding",
    "date_completed": "Date Read",
    "rating": "My Rating",
    "notes": "My Review",
    "shelf": "Exclusive Shelf",
}

# Field -> ordered synonyms (normalized, substring match) for generic CSVs.
_SYNONYMS = {
    "title": ["title", "book"],
    "author": ["author", "writer", "by"],
    "format": ["format", "binding", "edition type"],
    "date_completed": ["date read", "date finished", "finished", "date completed", "completed", "read date"],
    "rating": ["my rating", "rating", "stars", "score"],
    "notes": ["my review", "review", "notes", "comment"],
    "shelf": ["exclusive shelf", "shelf", "status"],
}

_BINDING_TO_FORMAT = {
    "kindle edition": "ebook", "ebook": "ebook", "kindle": "ebook",
    "paperback": "paperback", "mass market paperback": "paperback",
    "hardcover": "hardcover", "hardback": "hardcover",
    "audiobook": "audiobook", "audio cd": "audiobook", "audible audio": "audiobook", "audio": "audiobook",
}


@dataclass
class ParsedRow:
    raw_title: str
    raw_author: str
    raw_format: str          # normalized vocab; defaults to 'ebook'
    raw_date: str            # original text (may be '')
    date_completed: date | None  # parsed; None if blank/unparseable/future
    bad_date: bool           # raw_date non-empty but unparseable or future
    rating: int | None
    notes: str | None
    shelf: str               # lowercased exclusive shelf; '' when absent


def _norm(s: str | None) -> str:
    return " ".join((s or "").strip().lower().split())


def sniff_source(headers: list[str]) -> str:
    return "goodreads" if set(headers) >= _GOODREADS_SIGNATURE else "generic"


def suggest_mapping(headers: list[str], source: str) -> dict[str, str | None]:
    if source == "goodreads":
        present = set(headers)
        return {field: (col if col in present else None) for field, col in _GOODREADS_MAP.items()}
    norm_headers = [(set(_norm(h).split()), h) for h in headers]
    mapping: dict[str, str | None] = {}
    for field, syns in _SYNONYMS.items():
        match = None
        for syn in syns:
            syn_tokens = set(syn.split())
            for tokens, original in norm_headers:
                if syn_tokens <= tokens:  # all synonym words present as whole tokens
                    match = original
                    break
            if match:
                break
        mapping[field] = match
    return mapping
