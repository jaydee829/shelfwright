"""Backend-neutral pure helpers for the recommendation pipeline: parse the Analyst/Explorer
structured outputs and gather internal candidate ids. No ADK / Claude imports — both backends
reuse these."""

from __future__ import annotations

import json
import re

from agentic_librarian.mcp.server import get_unacted_suggestions, search_internal_database


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
    """Gather internal DB candidates from the Analyst's targets, de-duplicated, order preserved."""
    targets = coerce_schema_value(state.get("targets"))
    tropes = targets.get("tropes") or []
    styles = targets.get("styles") or []
    if not tropes and not styles:
        return []
    rows = search_internal_database(target_tropes=tropes, target_styles=styles)
    rows += get_unacted_suggestions(target_tropes=tropes, target_styles=styles)
    seen: list[str] = []
    for r in rows:
        wid = r.get("id")
        if wid and wid not in seen:
            seen.append(wid)
    return seen


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
