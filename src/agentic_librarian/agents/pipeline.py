"""Custom (non-LLM) steps of the recommendation SequentialAgent pipeline. Each writes its
result to session state via EventActions(state_delta=...) — direct ctx.session.state mutation
does NOT persist in ADK 2.1.0."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from agentic_librarian.mcp.server import (
    enrich_and_persist_work,
    get_unacted_suggestions,
    log_suggestion,
    search_internal_database,
)
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from typing_extensions import override


def coerce_schema_value(value) -> dict:
    """An LlmAgent output_schema/output_key result may arrive in state as a dict, a JSON string,
    or a Pydantic model. Normalize to a plain dict."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
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


class InternalCandidatesAgent(BaseAgent):
    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        ids = extract_candidate_ids(dict(ctx.session.state))
        existing = list(ctx.session.state.get("candidate_ids") or [])
        merged = existing + [i for i in ids if i not in existing]
        yield Event(author=self.name, actions=EventActions(state_delta={"candidate_ids": merged}))


class EnrichmentAgent(BaseAgent):
    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        candidate_ids = list(ctx.session.state.get("candidate_ids") or [])
        for title, author in extract_discovery_pairs(dict(ctx.session.state)):
            wid = enrich_and_persist_work(title, author)  # de-dups + persists; None on failure
            if wid and wid not in candidate_ids:
                candidate_ids.append(wid)
        yield Event(author=self.name, actions=EventActions(state_delta={"candidate_ids": candidate_ids}))


class LoggerAgent(BaseAgent):
    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        recommendation = ctx.session.state.get("recommendation") or ""
        candidate_ids = list(ctx.session.state.get("candidate_ids") or [])
        if recommendation and candidate_ids:
            # Log the first candidate as the acted suggestion; justification is the Critic's text.
            # TODO(spec5): candidate_ids is in gather order, not the Critic's ranked order — log the
            # Critic's actual top pick once the Critic emits a structured ranking, not just prose.
            log_suggestion(work_id=candidate_ids[0], context="recommendation", justification=recommendation[:1000])
        yield Event(
            author=self.name, actions=EventActions(state_delta={"logged": bool(recommendation and candidate_ids)})
        )
