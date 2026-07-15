"""Pure CSV parsing/normalization for bulk import (Spec 2026-06-18). No I/O — the highest
test-value surface, where Goodreads/generic format variability lives."""

from __future__ import annotations

import re
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


# Completion-date formats, tried in order. A deterministic explicit list, NOT dateutil:
# users can map any column here, and dateutil fills missing components from today's date
# (a mis-mapped rating cell "5" becomes the 5th of this month) — a wrong mapping must
# surface as bad_date in the report, never as plausible invented dates. Datetime-bearing
# formats (Libby exports "October 14, 2017 0:34") are truncated to the calendar date —
# the time-of-day and its unstated timezone are irrelevant here.
_DATE_FORMATS = (
    # Numeric: Goodreads, ISO date, US (day-first ambiguity is resolved as US).
    "%Y/%m/%d",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%y",
    "%m-%d-%Y",
    # Month-name: Libby timestamps, written-out dates, Excel DD-MMM-YYYY.
    "%B %d, %Y %H:%M",
    "%B %d, %Y",
    "%b %d, %Y %H:%M",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%d-%b-%Y",
)

# Gate for the fromisoformat fallback: a full YYYY-MM-DD prefix followed by a time part.
_ISO_DATETIME_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}[T ]")


@dataclass
class ParsedRow:
    raw_title: str
    raw_author: str  # parse-normalized primary author (#142, via _primary_author) — NOT the
    # verbatim CSV cell; a comma/'and'/'&'-joined cell is already collapsed here. Preview and
    # commit both call parse_rows, so they re-parse identically and the user previews exactly
    # what will be created.
    raw_format: str  # normalized vocab; defaults to 'ebook'
    raw_date: str  # original text (may be '')
    date_completed: date | None  # parsed; None if blank/unparseable/future
    bad_date: bool  # raw_date non-empty but unparseable or future
    rating: int | None
    notes: str | None
    shelf: str  # lowercased exclusive shelf; '' when absent


def _norm(s: str | None) -> str:
    return " ".join((s or "").strip().lower().split())


# Splits a raw author cell into candidate author segments: commas, or the explicit
# multi-author separators " and " / " & " (case-insensitive, requires surrounding spaces
# so it doesn't fire inside a name like "Andy" or "A&W"). Matched against the UNSTRIPPED
# text so a cell that is only a separator (" and ", " & ") still splits into empty segments
# rather than surviving as a bare "and"/"&" once outer whitespace is trimmed.
_AUTHOR_SPLIT = re.compile(r",|\s+and\s+|\s+&\s+", re.IGNORECASE)

# A segment reads as "a full name" (rather than a bare initial/given name fragment) when it
# has 2+ whitespace tokens and the last token isn't a single-letter initial like "K." —
# this is what distinguishes "Jane Doe, John Smith" (split) from "Le Guin, Ursula K."
# (a single "Last, First M." name — keep whole).
_INITIAL_TOKEN = re.compile(r"^[A-Za-z]\.?$")


def _is_full_name(segment: str) -> bool:
    tokens = segment.split()
    return len(tokens) >= 2 and not _INITIAL_TOKEN.match(tokens[-1])


def _primary_author(text: str) -> str:
    """Collapse a comma/'and'/'&'-joined author cell to its primary author.

    Import author cells are sometimes a single string holding multiple names or a
    duplicated name (observed prod artifact: 'Casualfarmer, CasualFarmer', one Goodreads
    cell stored verbatim). A dirty author identity defeats work get-or-create matching and
    can seed duplicate works. This normalizes at parse time, before raw_author reaches the
    matcher.

    Decision table:
      - Empty/whitespace-only -> "".
      - No comma/' and '/' & ' -> the trimmed text as-is.
      - Two or more separators, OR all segments case-insensitively equal (the
        'Casualfarmer, CasualFarmer' case), OR an explicit ' and '/' & ' separator ->
        the first segment.
      - Exactly one comma and BOTH segments look like full names (2+ words, not ending in
        a bare initial, e.g. "Jane Doe, John Smith") -> the first segment.
      - Exactly one comma otherwise -> the WHOLE CELL, unchanged. This is plausibly a
        "Last, First" single name (e.g. "Ware, Ruth", "Le Guin, Ursula K."); splitting a
        real person's name is worse than leaving a splittable one intact. Accepted
        residual: such cells keep their comma form and may still mint a variant author
        identity distinct from a "First Last"-formatted row for the same person — the
        works-merge tool is the backstop (spec 2026-07-14-works-merge-tool-design.md).
    """
    stripped = text.strip()
    if not stripped:
        return ""

    # Split the ORIGINAL (unstripped) text: the " and "/" & " separators require
    # surrounding whitespace to match, which a pre-strip would remove from a
    # separator-only cell like " and ", leaving a bare "and" that looks like a name.
    raw_segments = [seg.strip() for seg in _AUTHOR_SPLIT.split(text)]
    segments = [seg for seg in raw_segments if seg]
    if not segments:
        return ""  # cell was only separators, e.g. " and " / " & " / ", "
    if len(raw_segments) == 1:
        return segments[0]  # no separator matched -> as-is (trimmed)

    has_and_separator = bool(re.search(r"\s+(?:and|&)\s+", stripped, re.IGNORECASE))
    comma_count = stripped.count(",")

    # Checked BEFORE the multi-separator arm: a triple like 'Casualfarmer, CasualFarmer,
    # CasualFarmer' must collapse to one name, not fall into Last-First preservation.
    all_equal = len({seg.lower() for seg in segments}) == 1
    if all_equal:
        return segments[0]

    if comma_count >= 2 or has_and_separator:
        # Gemini review (#143): a "Last, First" PRIMARY followed by more authors
        # ('Ware, Ruth, John Smith') must keep the full primary name — truncating to the
        # bare surname 'Ware' is the least matchable identity of all. The first two comma
        # segments are one person when they DIFFER and don't both look like full names.
        if (
            comma_count >= 1
            and len(segments) >= 2
            and segments[0].lower() != segments[1].lower()
            and not (_is_full_name(segments[0]) and _is_full_name(segments[1]))
        ):
            return f"{segments[0]}, {segments[1]}"
        return segments[0]

    if comma_count == 1 and all(_is_full_name(seg) for seg in segments):
        return segments[0]

    # Single comma, not all-equal, not two full names -> plausibly "Last, First"; keep whole.
    return stripped


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
    d = None
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue
    if d is None and _ISO_DATETIME_PREFIX.match(text):
        # Machine ISO-8601 timestamps ('T' separator, offsets/'Z', fractional seconds) via
        # fromisoformat. The full-date prefix guard keeps it from resolving fragments like
        # "2017" or "20171014" — a partial cell must stay bad_date, never an invented date.
        try:
            d = datetime.fromisoformat(text).date()
        except ValueError:
            d = None
    if d is None or d > date.today():
        return None, True
    return d, False


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
                raw_author=_primary_author(_cell(row, mapping.get("author"))),
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
