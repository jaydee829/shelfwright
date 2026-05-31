# Spec 1 — Mesh Runtime Foundation

**Date:** 2026-05-30
**Status:** Approved design (pre-implementation)
**Part of:** ADR-035 phased mesh delivery — spec 1 of 4.

## Goal

Make the existing 4-agent recommendation mesh actually **run**. Today none of the
`LlmAgent`s have a model and there is no ADK Runner/entrypoint, so the mesh cannot
execute at all. This spec adds the minimal runtime so the Librarian executes a user
prompt and delegates to Analyst/Explorer/Critic, returning a final text response.

**Success = "runs & delegates" (smoke):** `run_recommendation(prompt)` returns a
non-empty response without error. Recommendation *quality* is explicitly out of scope
(Specs 2–4).

## Approach

**Approach A (approved):** one ADK `Runner` over the existing `create_agent_mesh()`
Librarian root agent; LLM-driven `AgentTool` delegation drives the specialists. Honors
ADR-019 (4-agent mesh) and ADR-020 (ADK delegation).

Rejected: (B) manual Python orchestration of the sub-agents and (C) ADK
`SequentialAgent` workflow — both discard the committed agent-delegation design.

## Components

### New module: `agents/runtime.py`
- `build_runner() -> Runner` — calls `create_agent_mesh()`, wraps the Librarian in a
  `Runner(agent=librarian, app_name="agentic_librarian", session_service=InMemorySessionService())`.
- `async def arun_recommendation(prompt, user_id="local", session_id=None) -> str` —
  creates a session, calls
  `runner.run_async(user_id, session_id, new_message=types.Content(role="user", parts=[types.Part(text=prompt)]))`,
  iterates events, and returns the text of the `event.is_final_response()` event (or a
  clear fallback string if there is none).
- `def run_recommendation(prompt) -> str` — `asyncio.run(arun_recommendation(prompt))`;
  the public synchronous entrypoint.

### Agent models (`agents/services.py`)
- Each `LlmAgent` receives `model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")`
  (same env var as the scouts). A single model for all agents in this spec; per-agent
  tuning is deferred.
- The Explorer remains a model-only agent with **no search tool** in this spec (search
  is Spec 2); it answers from training knowledge as a placeholder.

### Auth reconciliation
- ADK's Gemini model authenticates via `GOOGLE_API_KEY` (Gemini Developer API;
  `GOOGLE_GENAI_USE_VERTEXAI` stays false/unset).
- The project's `GOOGLE_SEARCH_API_KEY` is confirmed to have access to **both** the
  Custom Search API and the Gemini API in the GCP project, so it can serve as the ADK
  key.
- `runtime.py` ensures `GOOGLE_API_KEY` is populated at import: if unset, fall back to
  `GEMINI_API_KEY`, then `GOOGLE_SEARCH_API_KEY`.
- `.env.example` documents `GOOGLE_API_KEY` (optional when `GOOGLE_SEARCH_API_KEY` is set).

## Data flow

```
run_recommendation("something like Dune")
  → InMemorySessionService.create_session(...)
  → Runner.run_async(new_message=user prompt)
  → Librarian LLM runs, delegates via AgentTool to Analyst / Explorer / Critic
      (their DB FunctionTools query Postgres; on an empty/seed DB they return [] — ok)
  → Librarian composes a final response
  → return final_response text
```

## Error handling
- The entrypoint returns a clear message when there is no final response / no text parts.
- DB FunctionTools already return empties (not exceptions) on an empty DB.
- A single failing sub-agent or tool should not abort the whole run; log and continue so
  the Librarian still returns a response.

## Testing
- **Mock unit tests** (run in CI, no API/DB):
  - `build_runner()` constructs a `Runner` without error.
  - Every agent in `create_agent_mesh()` has a non-empty `model`.
  - `run_recommendation` returns the final text when `Runner.run_async` is mocked to
    yield a final-response event.
- **`@pytest.mark.api_dependent` live smoke** (manual; needs a key + DB up):
  - `run_recommendation("something like Dune")` returns a non-empty string.

## Out of scope (later specs)
- Explorer web-search grounding (Spec 2).
- Seeded-DB internal retrieval quality (Spec 3).
- Coherent end-to-end recommendation + Trope-RAG justification, e2e test (Spec 4).
- Per-agent model tuning, response streaming, multi-turn conversation state beyond a
  single session.
