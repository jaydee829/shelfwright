"""Pure shelf->destination routing for bulk import (Spec 2026-06-18, D4)."""

from __future__ import annotations

from agentic_librarian.imports.parsing import ParsedRow


def bucket(row: ParsedRow, *, import_to_read: bool, import_currently_reading: bool) -> tuple[str, str | None]:
    """Return (destination, skip_reason). destination is 'history' | 'suggestion' | 'skip';
    skip_reason is set only when destination == 'skip'."""
    shelf = row.shelf
    if shelf == "to-read":
        return ("suggestion", None) if import_to_read else ("skip", "to_read_opt_out")
    if shelf == "currently-reading":
        return ("suggestion", None) if import_currently_reading else ("skip", "currently_reading_opt_out")
    # 'read', a custom shelf, or no shelf (generic CSV) → a completed-read candidate.
    if row.date_completed is not None:
        return ("history", None)
    return ("skip", "bad_date" if row.bad_date else "no_completion_date")
