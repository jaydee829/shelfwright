"""Bulk reading-history import API (Spec 2026-06-18). Stateless preview/commit (the client
re-uploads the small CSV); per-row Cloud Tasks do the work. Firebase-gated like books.py."""

from __future__ import annotations

import csv
import io
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.imports import bucketing, parsing  # noqa: F401 (bucketing used by commit endpoint in next task)

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_ROWS = 2000


def _read_csv(raw: bytes) -> tuple[list[str], list[dict]]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    rows = list(reader)
    if not headers or not rows:
        raise HTTPException(status_code=422, detail="The file has no data rows.")
    return list(headers), rows


def _counts(parsed: list[parsing.ParsedRow]) -> dict:
    c = {"read_dated": 0, "read_undated": 0, "to_read": 0, "currently_reading": 0, "total": len(parsed)}
    for p in parsed:
        if p.shelf == "to-read":
            c["to_read"] += 1
        elif p.shelf == "currently-reading":
            c["currently_reading"] += 1
        elif p.date_completed is not None:
            c["read_dated"] += 1
        else:
            c["read_undated"] += 1
    return c


def _preview_row(p: parsing.ParsedRow) -> dict:
    return {
        "title": p.raw_title, "author": p.raw_author, "format": p.raw_format,
        "date_completed": p.date_completed.isoformat() if p.date_completed else None,
        "rating": p.rating, "shelf": p.shelf,
    }


@router.post("/import/preview")
async def preview(
    file: UploadFile = File(...),  # noqa: B008
    mapping: str | None = Form(None),  # noqa: B008 - JSON override when the user edits the map
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    headers, rows = _read_csv(await file.read())
    source = parsing.sniff_source(headers)
    suggested = parsing.suggest_mapping(headers, source)
    effective = json.loads(mapping) if mapping else suggested
    parsed = parsing.parse_rows(rows, effective)
    return {
        "source": source,
        "headers": headers,
        "suggested_mapping": suggested,
        "preview_rows": [_preview_row(p) for p in parsed[:5]],
        "counts": _counts(parsed),
    }
