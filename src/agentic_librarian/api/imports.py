"""Bulk reading-history import API (Spec 2026-06-18). Stateless preview/commit (the client
re-uploads the small CSV); per-row Cloud Tasks do the work. Firebase-gated like books.py."""

from __future__ import annotations

import csv
import io
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.db.models import ImportJob, ImportRow
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.imports import bucketing, parsing
from agentic_librarian.imports.tasks import enqueue_import_row

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_ROWS = 2000

db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (tests / shared-pool lifespan) — mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


_REQUIRED_FIELDS = ("title", "author", "date_completed")


def _read_csv(raw: bytes) -> tuple[list[str], list[dict]]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    rows = list(reader)
    if not headers or not rows:
        raise HTTPException(status_code=422, detail="The file has no data rows.")
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"File has {len(rows)} rows; the limit is {MAX_ROWS}.")
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


@router.post("/import/commit")
async def commit(
    file: UploadFile = File(...),  # noqa: B008
    mapping: str = Form(...),  # noqa: B008
    import_to_read: bool = Form(False),  # noqa: B008
    import_currently_reading: bool = Form(False),  # noqa: B008
    original_filename: str | None = Form(None),  # noqa: B008
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    parsed_mapping = json.loads(mapping)
    missing = [f for f in _REQUIRED_FIELDS if not parsed_mapping.get(f)]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required column mapping: {', '.join(missing)}")

    headers, rows = _read_csv(await file.read())
    source = parsing.sniff_source(headers)
    parsed = parsing.parse_rows(rows, parsed_mapping)

    enqueue_ids: list[str] = []
    with db_manager.get_session() as session:
        job = ImportJob(
            user_id=user.id, source=source, original_filename=original_filename, total_rows=len(parsed)
        )
        session.add(job)
        session.flush()  # populate job.id for the ImportRow FK
        to_enqueue: list[ImportRow] = []
        for p in parsed:
            destination, skip_reason = bucketing.bucket(
                p, import_to_read=import_to_read, import_currently_reading=import_currently_reading
            )
            row = ImportRow(
                import_job_id=job.id, user_id=user.id,
                raw_title=p.raw_title, raw_author=p.raw_author, raw_format=p.raw_format,
                raw_date=p.raw_date, date_completed=p.date_completed if destination == "history" else None,
                rating=p.rating, notes=p.notes, destination=destination, shelf=p.shelf,
                status="skipped" if destination == "skip" else "pending",
                skip_reason=skip_reason,
            )
            session.add(row)
            if destination != "skip":
                to_enqueue.append(row)
        session.flush()  # one flush populates all ImportRow.id values
        enqueue_ids = [str(row.id) for row in to_enqueue]
        job_id = str(job.id)

    for rid in enqueue_ids:
        try:
            enqueue_import_row(rid)
        except Exception:  # noqa: BLE001 - a failed enqueue leaves the row 'pending' for retry
            logger.exception("import-row enqueue failed for row %s", rid)

    return {"import_job_id": job_id, "total_rows": len(parsed), "enqueued": len(enqueue_ids)}
