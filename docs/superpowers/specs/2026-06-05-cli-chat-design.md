# CLI Chat Harness — Design

**Date:** 2026-06-05
**Status:** Approved (brainstormed with user)
**Branch:** `spec/cli-chat`

## Problem

There is no command-line way to exercise the conversational piece of the recommendation
system. Today's options are: a one-shot `run_recommendation()` via `python -c`, an
interactive-Python session around `LibrarianConversation` (ADK only), or the
`api_dependent` e2e tests. None of them give a usable multi-turn chat, and the Claude
backend has no conversational mode at all — its `RecommendationBackend` protocol is
one-shot only, implemented as a fixed Analyst→Explorer→Critic pipeline of independent
`query()` calls.

## Goals (user decisions)

1. **Multi-turn chat on BOTH backends** (ADK and Claude), plus the existing one-shot
   pipeline behind a flag. The Claude backend gains a true conversational mode.
2. **Visibility**: print final replies plus a compact trace of key events (tool calls,
   agent delegations) — a hollow run must look different from a real one.
3. **Invocation**: a `librarian` console script (`[project.scripts]`), runnable as
   `docker exec -it agentic_librarian_app librarian ...`.
4. **MLflow conversation capture**: each conversation is an MLflow run, with a
   graceful-degradation posture (logging must never block or kill a chat).

## Approach (chosen: A)

Extend the `RecommendationBackend` strategy seam (ADR-041) with a conversation method,
so "multi-turn" means the same thing on both backends: a stateful Librarian session that
calls DB/web tools on demand. Rejected alternatives: (B) CLI-managed transcript replay
over the one-shot pipeline — re-runs the full pipeline every turn (slow, quota-hungry,
logs a spurious Suggestion per turn, simulated rather than real conversation); (C) hybrid
with transcript-replay only on Claude — two different conversation semantics muddies what
the harness tests.

## Architecture

### 1. Protocol extension — `agents/backends/__init__.py`

```python
@runtime_checkable
class BackendConversation(Protocol):
    def send(self, message: str) -> str: ...
    def close(self) -> None: ...

class RecommendationBackend(Protocol):
    name: str
    def run_recommendation(self, prompt: str, user_id: str = "local") -> str: ...
    def start_conversation(
        self, user_id: str = "local",
        on_event: Callable[[str, str], None] | None = None,
    ) -> BackendConversation: ...
```

`on_event(kind, detail)` is the visibility hook — e.g. `("tool",
"search_internal_database")`, `("agent", "Explorer")`. Structured-but-minimal; the CLI
owns formatting.

### 2. ADK adapter — `agents/backends/adk.py` + small `agents/runtime.py` touch

`ADKBackend.start_conversation` wraps the existing `LibrarianConversation` (ADR-036).
The only `runtime.py` change: `LibrarianConversation.asend` gains an optional event
callback, fired from the `run_async` event stream it already iterates (function calls /
agent-author transitions). Default `None` — zero behavior change for existing callers.

### 3. Claude conversation — `agents/backends/claude.py` (full mesh parity)

New `ClaudeConversation` holding one persistent `ClaudeSDKClient` session
(verified current via Context7 `/anthropics/claude-agent-sdk-python`: each
`client.query()` continues the same session; `receive_response()` yields messages
until the turn's `ResultMessage`).

**Mesh parity (user decision, 2026-06-05):** the conversational Librarian delegates to
the SAME specialist mesh ADK uses, via programmatic SDK subagents
(`ClaudeAgentOptions(agents={...: AgentDefinition(...)})`, invoked through the `Task`
tool — the direct analogue of ADK's `AgentTool`). A single-agent variant was rejected:
it can do the same tasks but doesn't exercise the specialist prompts, and cross-backend
MLflow comparisons would conflate backend with architecture.

- Subagents reuse the specialist prompts verbatim, tool-scoped like the ADK mesh:
  `analyst` (ANALYST_INSTRUCTION; `get_user_trope_preferences`), `explorer`
  (EXPLORER_INSTRUCTION; `WebSearch`), `critic` (CRITIC_INSTRUCTION;
  `search_internal_database`, `get_work_details`, `check_reading_history`).
- The Librarian session: `system_prompt=LIBRARIAN_INSTRUCTION` (delegation-flavored,
  mirroring the ADK Librarian's inline instruction), the in-process librarian MCP
  server (`build_librarian_mcp_server()`), `allowed_tools` = `"Task"` + the Librarian's
  direct tools (`get_unacted_suggestions`, `update_reading_status`,
  `update_suggestion_status`, `log_suggestion`), model from `CLAUDE_MODEL`
  (default `claude-sonnet-4-6`).
- `send()` = `query()` + collect the turn result from `receive_response()`; each
  `ToolUseBlock` fires `on_event` — a `Task` block maps to `("agent",
  <subagent_type>)`, everything else to `("tool", <name>)`, matching the ADK trace.
- **VERIFY on first live run** (REC-019 pattern): subagents see the parent's
  in-process MCP server via `AgentDefinition.mcpServers=["librarian"]` — if the
  in-process server is not visible to subagents, fall back to scoping those tools on
  the Librarian and letting the critic receive tool RESULTS in its Task prompt.
- The SDK is async; the REPL is sync. The session lives on a **background event-loop
  thread** (the same running-loop constraint `ClaudeGroundedLLM.generate` solved in
  PR #26 — follow that precedent; `asyncio.run` per send would tear down the session).
- `close()` disconnects the client and stops the loop thread.
- The existing one-shot pipeline (`_arun`) is untouched.

### 4. CLI — new `src/agentic_librarian/cli.py` + `[project.scripts]`

argparse only (no new dependencies). `librarian = "agentic_librarian.cli:main"`.

```
librarian                          # REPL (multi-turn, configured backend)
librarian --once "find me ..."     # one-shot pipeline (run_recommendation)
librarian --backend adk|claude     # override AGENT_BACKEND for this run
librarian --user-id NAME           # default "local"
librarian --quiet                  # replies only, no event trace
librarian --no-mlflow              # disable conversation capture
```

Data flow: CLI → `get_backend()` → conversation/one-shot; event lines print dimmed as
they arrive; the reply prints when the turn completes. Startup banner states backend,
model, and MLflow run id (self-documenting test runs). Commands: `/quit` (also
Ctrl-D / Ctrl-C at prompt). No `/history`, no `/save` — capture handles that (YAGNI).

### 5. MLflow conversation capture — `ConversationRecorder` in `src/agentic_librarian/chat_recorder.py`

- **One conversation = one MLflow run**, experiment `librarian_conversations`;
  `--once` logs the same way with `mode=one-shot`.
- **Params**: backend, model, user_id, mode. **Metrics**: `turns`, per-turn
  `latency_s` (step-indexed), total duration. **Artifacts**: `transcript.jsonl`
  (per turn: user text, reply, event trace, latency), uploaded at close; buffered
  locally during the chat so a crash keeps what's written.
- **Ownership**: CLI layer only — the CLI already sees every message, reply, and event
  via `on_event`; backends stay pure.
- **Degradation posture** (the in-container MLflow server has bitten us before —
  DNS-rebinding 403, bugs.md 2026-05-31): tracking server unreachable → one warning
  line, chat continues, transcript still written to a local fallback
  `.chat_logs/<timestamp>.jsonl` (gitignored). MLflow errors mid-run are caught and
  warned, never raised into the REPL.

## Error handling

- **A failed turn never kills the REPL**: backend exception → `error: <type>: <msg>`
  printed, recorded in the transcript, prompt continues. (Transient Gemini 5xx are
  already retried inside via `llm_retry`.)
- **Ctrl-C during a turn** aborts the turn, not the session; Ctrl-C/Ctrl-D at the
  prompt exits cleanly.
- **Clean shutdown** (`finally`): `conversation.close()` → recorder flush → MLflow run
  ended with status.
- **Startup failures fail fast**: unknown backend (existing `ValueError`), missing
  `claude` CLI auth, DB unreachable → clear message, nonzero exit, no broken REPL.

## Testing (TDD; offline-deterministic except the live smoke)

1. **Protocol conformance**: both backends satisfy the extended protocol
   (mirrors `test_backends.py`).
2. **ADK adapter**: fake runner yielding scripted events → `on_event` fires for tool
   calls/agent transfers; final text returned (extends `test_agent_runtime.py` fakes).
3. **ClaudeConversation**: duck-typed fake SDK client (pattern from
   `test_claude_backend.py`) → session reused across two `send()`s, ToolUseBlock →
   event mapping, clean `close()`.
4. **CLI**: injected stdin/stdout + fake backend → REPL loop, `--once`, `--backend`
   override, `--quiet`, error-turn survival.
5. **Recorder**: local file-store MLflow (the `local_mlflow_tracking` fixture pattern)
   → params/metrics/artifact written; unreachable-server path degrades to local jsonl.
6. **Live smoke** (`api_dependent`, manual): 2-turn conversation per backend asserting
   a non-empty, contextually-aware second reply.

## Out of scope

- Changing the Claude one-shot pipeline (ADR-040 semantics stay).
- Streaming token-by-token output, `/history`-style REPL commands, transcript replay.
- The Phase-4 web UI (this harness is the stopgap until then).
- Embeddings remain Gemini regardless of backend (ADR-044).

## References

ADR-036 (conversational Librarian), ADR-040 (one-shot pipeline), ADR-041 (backend
strategy seam), ADR-044 (GroundedLLM seam / PR #26 async precedent), REC-019
(`WebSearch` tool name verified live), bugs.md 2026-05-31 (MLflow 403 / degradation
rationale).
