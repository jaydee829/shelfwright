# Spec 1 — Mesh Runtime Foundation

**Date:** 2026-05-30
**Status:** Approved design (pre-implementation)
**Part of:** ADR-035 phased mesh delivery — spec 1 of 4.

## Goal

Make the existing 4-agent recommendation mesh actually **run**, as a **multi-turn
conversation** with the Librarian — the user talks to a librarian who remembers the
exchange and knows their reading history. Today none of the `LlmAgent`s have a model and
there is no ADK Runner/entrypoint, so the mesh cannot execute at all. This spec adds the
minimal runtime: assign models, host the Librarian in an ADK Runner with a reusable
session, and expose a conversation API that delegates to Analyst/Explorer/Critic.

**Success = "runs & delegates" (smoke):** a conversation can be started and one or more
messages sent, each returning a non-empty response without error, with the session
remembering prior turns. Recommendation *quality* and response styling are out of scope
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
- `class LibrarianConversation` — holds the `Runner` plus a fixed `(user_id, session_id)`:
  - `async def asend(message) -> str` / `def send(message) -> str` — sends one turn via
    `runner.run_async(user_id, session_id, new_message=types.Content(role="user", parts=[types.Part(text=message)]))`,
    iterates events, returns the `event.is_final_response()` text (or a clear fallback if
    none). **Reusing the same session across calls is what gives the Librarian
    conversational memory.**
- `async def start_conversation(user_id="local") -> LibrarianConversation` — creates the
  session and returns a conversation handle.
- `def run_recommendation(prompt) -> str` — one-shot convenience: start a conversation
  and send a single message (`asyncio.run` wrapper). Handy for tests and simple queries.

The `SessionService` is `InMemorySessionService` — conversations are remembered within a
run, not across restarts. Durable "knows everything you've read" comes from the Postgres
reading-history DB via the agents' tools, not the session. `DatabaseSessionService`
(Postgres-backed, resumable conversations) is a deferred upgrade.

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
conv = start_conversation(user_id="local")     # creates session S
conv.send("something like The Way of Kings but grittier")
  → Runner.run_async(session S, user prompt)
  → Librarian LLM runs, delegates via AgentTool to Analyst / Explorer / Critic
      (their DB FunctionTools query Postgres; on an empty/seed DB they return [] — ok)
  → final response text returned
conv.send("I already read The Blade Itself")    # same session S → Librarian recalls the prior turn
  → ... → updated response
```

`run_recommendation("...")` is the single-turn shortcut over the same path.

## Error handling
- The entrypoint returns a clear message when there is no final response / no text parts.
- DB FunctionTools already return empties (not exceptions) on an empty DB.
- A single failing sub-agent or tool should not abort the whole run; log and continue so
  the Librarian still returns a response.

## Testing
- **Mock unit tests** (run in CI, no API/DB):
  - `build_runner()` constructs a `Runner` without error.
  - Every agent in `create_agent_mesh()` has a non-empty `model`.
  - A conversation returns the final text when `Runner.run_async` is mocked to yield a
    final-response event; two `send` calls reuse the same `session_id` (memory).
  - `run_recommendation` returns the final text (one-shot path).
- **`@pytest.mark.api_dependent` live smoke** (manual; needs a key + DB up):
  - `run_recommendation("something like Dune")` returns a non-empty string.
  - A two-turn conversation works (the second turn can reference the first).

## Out of scope (later specs)
- Explorer web-search grounding (Spec 2).
- Seeded-DB internal retrieval quality (Spec 3).
- Coherent end-to-end recommendation, response styling (authors vs books vs
  authors + an example book), and Trope-RAG justification; e2e test (Spec 4).
- Per-agent model tuning, response streaming.
- Persistent / resumable conversations across app restarts (`DatabaseSessionService`) —
  a later upgrade; Spec 1 uses in-memory sessions.
