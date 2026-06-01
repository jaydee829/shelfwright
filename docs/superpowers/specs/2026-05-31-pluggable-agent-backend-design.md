# Spec: Pluggable Agent Backend (ADK + Claude Agent SDK) — Design

**Status:** Approved (2026-05-31)
**Branch:** `spec/pluggable-agent-backend` (off `spec/e2e-recommendation`, Spec 4 unmerged)
**Predecessors:** Specs 1–4 (the ADK/Gemini recommendation mesh + pipeline).

## Goal

Add a **Claude Agent SDK** backend for `run_recommendation` that draws on the user's **Max
subscription quota**, selectable by config, **alongside** the existing Google ADK/Gemini backend
— without throwing away the ADK work. The recurring Gemini free-tier 429 wall is the motivation;
the Max plan provides quota the user already pays for.

**Scope (this spec):** the backend abstraction (Strategy seam + factory + config flag) and a
**Claude backend implementing only the one-shot `run_recommendation`** pipeline at parity with the
ADK one. The conversational Librarian on Claude, security hardening, and removing ADK are out of
scope (later specs).

## Why a second backend (and why now)

The ADK mesh calls models via **API keys** (Gemini), so it cannot reach the Max **subscription**
quota — that is only accessible through the Claude Agent SDK's auto-detected Claude Code auth
(*"if you've authenticated Claude Code… the SDK will use that authentication automatically"*).
Roughly 70% of the system (MCP tools, DB, pgvector, scouts, ETL, `persist_enriched_work`, Pydantic
schemas) is already backend-agnostic, so a second backend is mostly the agent-orchestration layer.

## Architecture: the backend seam

Abstract at the **entrypoint**, not the individual agent (ADK `LlmAgent`/`SequentialAgent` and
Claude `query()`/subagents are too different to share an agent interface without leaking).

```
RecommendationBackend (Protocol)              AGENT_BACKEND = adk (default) | claude
    run_recommendation(prompt: str, user_id: str = "local") -> str
     ├── ADKBackend     -> existing create_recommendation_pipeline + Runner (Gemini, API key)
     └── ClaudeBackend  -> Claude Agent SDK pipeline (Claude, Max-subscription quota)
```

- `agents/runtime.py::run_recommendation` becomes a thin dispatch: `get_backend().run_recommendation(prompt, user_id)`.
- A factory `agents/backends/__init__.py::get_backend()` reads `AGENT_BACKEND` and returns the
  configured backend (default `adk`, so existing behavior is unchanged).
- `ADKBackend` wraps the **existing** pipeline + runner code verbatim — zero behavior change when
  `AGENT_BACKEND=adk`. The conversational path (`LibrarianConversation`, `start_conversation`,
  `build_runner`) is untouched and remains ADK-only for now.

This mirrors the existing `ScoutManager` strategy pattern.

## The Claude backend (one-shot pipeline)

The Claude backend is **explicit Python sequencing** of Claude Agent SDK `query()` calls — the
same deterministic philosophy adopted for the ADK pipeline in Spec 4 (ADR-040), and simpler (no
`SequentialAgent`/`state_delta`). Each step shares an in-Python state dict:

| # | Step | Mechanism |
|---|------|-----------|
| 1 | **Analyst** | `query()` with the shared Analyst prompt + the `get_user_trope_preferences` tool → `Targets` (structured output). |
| 2 | **Internal candidates** | Plain Python: call `search_internal_database` + `get_unacted_suggestions`, de-dupe ids (reuse the Spec 4 helper logic). |
| 3 | **Explorer** | `query()` with **Claude's web search tool** (replaces Gemini `google_search`) + the shared Explorer prompt → discoveries (`Discoveries`). |
| 4 | **Enrichment** | Plain Python: `enrich_and_persist_work(title, author)` per discovery (de-dup + persist, unchanged). |
| 5 | **Critic** | `query()` with the shared Critic (Trope-RAG) prompt + the DB tools (`search_internal_database`, `get_work_details`, `check_reading_history`) → recommendation text. |
| 6 | **Logger** | Plain Python: `log_suggestion(work_id=candidate_ids[0], …)`. |

`run_recommendation` returns the Critic's recommendation text. Steps 2/4/6 are pure Python (no
LLM) — the same logic as the ADK custom agents (`extract_candidate_ids`, `extract_discovery_pairs`,
the logging), reused so the two backends share the non-LLM glue where practical.

**Tools exposed to Claude** via `create_sdk_mcp_server` (in-process) wrapping the **same functions**
the ADK backend uses (`@tool`-adapted), with `allowed_tools=["mcp__librarian__…"]` per step.

## Shared layer (factored out, used by both backends)

- **Tools** — the `mcp/server.py` functions. ADK wraps with `FunctionTool`; Claude with
  `create_sdk_mcp_server`. One definition, two thin adapters, both **in-process** (so the
  `set_db_manager` test injection keeps working — a subprocess MCP server would break it).
- **Prompts/roles** — extract the Analyst/Explorer/Critic instruction text from
  `agents/services.py` into a neutral `agents/prompts.py`; both backends use the identical wording
  (prevents drift). The ADK agents import their instructions from there.
- **Schemas** — `agents/schemas.py` (`Targets`, `Discoveries`) reused for structured output on
  both backends.
- **Pure helpers** — the Spec 4 functions `coerce_schema_value`, `extract_candidate_ids`,
  `extract_discovery_pairs` currently live in `agents/pipeline.py`, which imports ADK. Move them to
  a **backend-neutral module** (e.g. `agents/candidates.py`) so the Claude backend reuses them
  **without importing ADK**; `pipeline.py` re-imports them from there (the existing tests follow).
- **Config** — a small `config.py` (or `agents/backends/config.py`): `AGENT_BACKEND`, model names,
  reusing the existing env-var conventions (`GEMINI_MODEL`, `EXPLORER_MODEL`, …).

## Web search & embeddings

- **Web search:** the Claude backend's Explorer uses **Claude's web search tool**; the ADK
  backend keeps Gemini `google_search`. Both produce the same `Discoveries` shape.
- **Embeddings stay Gemini.** `search_internal_database` embeds the query trope via
  `gemini-embedding-001` (pgvector). So even the Claude backend calls **Gemini for embeddings** —
  acceptable: embeddings are a separate, low-volume quota (not the generation 429 wall), and
  migrating embedding providers is out of scope.

## Devcontainer / dependencies

- Add the **`claude` CLI** and the **`claude-agent-sdk`** Python package to the devcontainer; the
  CLI is authenticated **in-container** (the chosen approach) so `ClaudeBackend` draws on Max
  quota. `claude-agent-sdk` is added under an optional extra (e.g. `[claude]`) so the ADK-only
  install is unaffected.
- `ADKBackend` is unchanged and remains the default; nothing about the ADK path depends on the
  Claude CLI being present.

## Error handling

- `get_backend()` validates `AGENT_BACKEND`; an unknown value raises a clear error. If
  `AGENT_BACKEND=claude` but the `claude` CLI/SDK is unavailable/unauthenticated, the backend
  surfaces a clear actionable error (it does **not** silently fall back to ADK — explicit is safer).
- The Claude pipeline degrades like the ADK one: a discovery that fails to enrich is skipped;
  no candidates → a graceful "no strong match"; the final text is always returned (never an empty
  sentinel without explanation).

## Testing

- **Backend-neutral contract test** — a `db_integration` test that seeds the fixture and asserts
  `run_recommendation` (via `get_backend()`) returns a justified, logged recommendation. Runs
  against the configured backend; defaults to `adk` in CI.
- **ADK path** — all existing offline/`db_integration`/`api_dependent` tests unchanged (default
  backend); the seam refactor must not change ADK behavior (existing tests are the guard).
- **Claude path** — an `api_dependent` e2e on the Claude backend (seeded DB, real Claude calls).
  On **Max quota** this may actually run end-to-end (unlike the Gemini e2e that has been
  429-blocked) — a real validation. Excluded from CI (api_dependent).
- **Unit** — `get_backend()` returns the right backend per `AGENT_BACKEND`; prompt/schema modules
  import cleanly; the Claude tool-adapter wraps the shared functions.

## Files (anticipated)

- **Create:** `agents/backends/__init__.py` (protocol + `get_backend()`), `agents/backends/adk.py`
  (`ADKBackend` wrapping existing code), `agents/backends/claude.py` (`ClaudeBackend` + the Claude
  tool adapter), `agents/prompts.py` (shared instruction text), `agents/candidates.py` (the moved
  pure helpers), a backend-neutral contract test, the Claude `api_dependent` e2e.
- **Modify:** `agents/runtime.py` (`run_recommendation` dispatches via `get_backend()`),
  `agents/services.py` (import instructions from `agents/prompts.py`), `pyproject.toml`
  (`claude-agent-sdk` optional extra), devcontainer (claude CLI + sdk),
  `docs/project_notes/decisions.md` (an ADR for the dual-backend architecture).
- **Unchanged:** the MCP tool functions, DB, pgvector, scouts, ETL, `persist_enriched_work`,
  embeddings, the conversational Librarian path.

## Out of scope (later specs)

Conversational Librarian on Claude; security hardening (SEC-001/002); migrating embeddings off
Gemini; removing or deprecating the ADK backend; production deployment/ops of the Claude auth.

## Success criteria

1. `AGENT_BACKEND=adk` (default) → existing behavior, all current tests green (no regression).
2. `AGENT_BACKEND=claude` → `run_recommendation` runs the Claude-SDK pipeline end-to-end on Max
   quota and returns a Trope-RAG-justified, logged recommendation, reusing the shared MCP tools.
3. The Analyst/Explorer/Critic prompts and the `Targets`/`Discoveries` schemas are shared by both
   backends (single source of truth; no duplicated wording).
4. The seam refactor changes no ADK behavior; tool `set_db_manager` test injection still works
   (in-process tool wrapping).
5. A backend-neutral contract test passes on the ADK backend in CI; an `api_dependent` Claude e2e
   exists (and ideally runs on Max quota).
