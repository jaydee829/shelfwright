"""Custom (non-LLM) steps of the recommendation SequentialAgent pipeline. Each writes its
result to session state via EventActions(state_delta=...) — direct ctx.session.state mutation
does NOT persist in ADK 2.1.0."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from typing_extensions import override

from agentic_librarian.agents.candidates import extract_candidate_ids, extract_discovery_pairs
from agentic_librarian.agents.services import AnalystAgent, CriticAgent, ExplorerAgent
from agentic_librarian.mcp.server import (
    enrich_and_persist_work,
    log_suggestion,
)


class InternalCandidatesAgent(BaseAgent):
    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # to_thread: these are blocking DB calls; don't stall the ADK event loop.
        ids = await asyncio.to_thread(extract_candidate_ids, dict(ctx.session.state))
        existing = list(ctx.session.state.get("candidate_ids") or [])
        merged = existing + [i for i in ids if i not in existing]
        yield Event(author=self.name, actions=EventActions(state_delta={"candidate_ids": merged}))


class EnrichmentAgent(BaseAgent):
    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        candidate_ids = list(ctx.session.state.get("candidate_ids") or [])
        for title, author in extract_discovery_pairs(dict(ctx.session.state)):
            # to_thread: enrich makes blocking network + DB calls; don't stall the event loop.
            wid = await asyncio.to_thread(enrich_and_persist_work, title, author)  # de-dups + persists; None on failure
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
            await asyncio.to_thread(
                log_suggestion, work_id=candidate_ids[0], context="recommendation", justification=recommendation[:1000]
            )
        yield Event(
            author=self.name, actions=EventActions(state_delta={"logged": bool(recommendation and candidate_ids)})
        )


def create_recommendation_pipeline() -> SequentialAgent:
    """The fixed-order recommendation pipeline (ADR-035 Spec 4). SequentialAgent logs a benign
    deprecation warning in 2.1.0 (the Workflow replacement is not shipped); ignore it."""
    return SequentialAgent(
        name="RecommendationPipeline",
        sub_agents=[
            AnalystAgent(),
            InternalCandidatesAgent(name="InternalCandidates"),
            ExplorerAgent(),
            EnrichmentAgent(name="Enrichment"),
            # output_key is essential: it writes the Critic's recommendation into
            # state["recommendation"], which run_recommendation returns and the Logger reads.
            CriticAgent(output_key="recommendation"),
            LoggerAgent(name="Logger"),
        ],
    )
