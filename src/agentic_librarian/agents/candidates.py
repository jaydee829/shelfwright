"""Backend-neutral pure helpers for the recommendation pipeline: parse the Analyst/Explorer
structured outputs and gather internal candidate ids. No ADK / Claude imports — both backends
reuse these."""

from __future__ import annotations

import json
import re

from agentic_librarian.mcp.server import (
    get_active_suggestion_work_ids,
    get_read_status,
    search_internal_database,
)


def coerce_schema_value(value) -> dict:
    """An LlmAgent output_schema/output_key result may arrive in state as a dict, a JSON string,
    or a Pydantic model. Normalize to a plain dict."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        # LLMs (incl. the Explorer, which has no output_schema) often wrap JSON in a ```json ... ```
        # fence despite instructions — strip it before parsing.
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return {}
        # A valid JSON string can decode to a list/scalar; the callers always do .get(), and the
        # annotation promises a dict, so coerce anything non-dict to {}.
        return parsed if isinstance(parsed, dict) else {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {}


def extract_candidate_ids(state: dict) -> list[str]:
    """Gather internal DB candidates from the Analyst's targets, de-duplicated, order preserved.
    The Analyst's session_constraints become negative retrieval targets (#125: 'less fantasy'
    must exclude structurally), and actively-suggested works never re-enter a fresh set."""
    targets = coerce_schema_value(state.get("targets"))
    tropes = targets.get("tropes") or []
    styles = targets.get("styles") or []
    constraints = targets.get("session_constraints") or []
    if not tropes and not styles:
        return []
    rows = search_internal_database(target_tropes=tropes, target_styles=styles, exclude_tropes=constraints or None)
    suggested = get_active_suggestion_work_ids()
    seen: list[str] = []
    for r in rows:
        wid = r.get("id")
        if wid and wid not in seen and wid not in suggested:
            seen.append(wid)
    return seen


def _candidate_view(r: dict) -> dict:
    """Normalize a search row into the common candidate shape."""
    return {
        "id": r.get("id"),
        "title": r.get("title"),
        "authors": r.get("authors") or [],
        "genres": r.get("genres") or [],
        "description": r.get("description") or "",
    }


def curate_candidates(
    target_tropes: list[str],
    target_styles: list[str] | None = None,
    limit: int = 10,
    exclude_tropes: list[str] | None = None,
    exclude_styles: list[str] | None = None,
) -> dict:
    """Deterministic, read-status-aware candidate set for recommendations (spec A1/A3 + #125).
    Vector-searches the catalog (relevance-ranked, negative targets honored), DROPS books
    finished <2y ago AND books already pitched with an unresolved suggestion (never re-offer
    what the user hasn't reacted to), orders unread-first, and reports has_unread so the
    caller can fall back to the Explorer for a fresh discovery."""
    rows = search_internal_database(
        target_tropes=target_tropes,
        target_styles=target_styles,
        limit=limit,
        exclude_tropes=exclude_tropes,
        exclude_styles=exclude_styles,
    )
    suggested = get_active_suggestion_work_ids()
    by_id: dict[str, dict] = {}
    for r in rows:
        wid = r.get("id")
        if wid and wid not in by_id and wid not in suggested:
            by_id[wid] = r
    if not by_id:
        return {"candidates": [], "has_unread": False, "unread_count": 0, "reread_count": 0}

    status = get_read_status(list(by_id.keys()))
    unread: list[dict] = []
    reread: list[dict] = []
    for wid, r in by_id.items():
        st = status.get(wid) or {}
        if st.get("status") == "Read":
            if not st.get("is_re_read_candidate"):
                continue  # finished <2y ago: neither new nor a valid re-read — drop
            reread.append(
                {
                    **_candidate_view(r),
                    "read_status": "reread",
                    "last_read": st.get("last_read"),
                    "rating": st.get("rating"),
                }
            )
        else:
            unread.append({**_candidate_view(r), "read_status": "new", "last_read": None, "rating": None})

    candidates_out = (unread + reread)[:limit]
    return {
        "candidates": candidates_out,
        "has_unread": bool(unread),
        "unread_count": len(unread),
        "reread_count": len(reread),
    }


def extract_discovery_pairs(state: dict) -> list[tuple[str, str]]:
    """Pull (title, author) pairs out of the Explorer's structured discoveries."""
    disc = coerce_schema_value(state.get("discoveries"))
    pairs = []
    for raw in disc.get("books") or []:
        book = raw if isinstance(raw, dict) else coerce_schema_value(raw)
        title, author = book.get("title"), book.get("author")
        if title and author:
            pairs.append((title, author))
    return pairs
