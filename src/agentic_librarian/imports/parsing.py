"""Pure CSV parsing/normalization for bulk import (Spec 2026-06-18). No I/O — the highest
test-value surface, where Goodreads/generic format variability lives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

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
    "kindle edition": "ebook",
    "ebook": "ebook",
    "kindle": "ebook",
    "paperback": "paperback",
    "mass market paperback": "paperback",
    "hardcover": "hardcover",
    "hardback": "hardcover",
    "audiobook": "audiobook",
    "audio cd": "audiobook",
    "audible audio": "audiobook",
    "audio": "audiobook",
}


@dataclass
class ParsedRow:
    raw_title: str
    raw_author: str
    raw_format: str  # normalized vocab; defaults to 'ebook'
    raw_date: str  # original text (may be '')
    date_completed: date | None  # parsed; None if blank/unparseable/future
    bad_date: bool  # raw_date non-empty but unparseable or future
    rating: int | None
    notes: str | None
    shelf: str  # lowercased exclusive shelf; '' when absent


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


def _cell(row: dict, col: str | None) -> str:
    if not col:
        return ""
    return (row.get(col) or "").strip()


def _parse_date(text: str) -> tuple[date | None, bool]:
    """Return (date or None, bad_date). bad_date is True only when text is present but
    unusable (unparseable or in the future). A blank string is (None, False)."""
    if not text:
        return None, False
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            d = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if d > date.today():
            return None, True
        return d, False
    return None, True


def _parse_rating(text: str) -> int | None:
    try:
        n = int(text)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 5 else None  # Goodreads 0 = unrated; out-of-range dropped


def _normalize_format(text: str) -> str:
    return _BINDING_TO_FORMAT.get(_norm(text), "ebook")


def parse_rows(rows: list[dict], mapping: dict[str, str | None]) -> list[ParsedRow]:
    out: list[ParsedRow] = []
    for row in rows:
        raw_date = _cell(row, mapping.get("date_completed"))
        parsed_date, bad = _parse_date(raw_date)
        notes = _cell(row, mapping.get("notes")) or None
        out.append(
            ParsedRow(
                raw_title=_cell(row, mapping.get("title")),
                raw_author=_cell(row, mapping.get("author")),
                raw_format=_normalize_format(_cell(row, mapping.get("format"))),
                raw_date=raw_date,
                date_completed=parsed_date,
                bad_date=bad,
                rating=_parse_rating(_cell(row, mapping.get("rating"))),
                notes=notes,
                shelf=_norm(_cell(row, mapping.get("shelf"))),
            )
        )
    return out
