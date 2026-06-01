# Pluggable Agent Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Claude Agent SDK backend for `run_recommendation` (Max-subscription quota), selectable by `AGENT_BACKEND`, alongside the existing ADK/Gemini backend, sharing the MCP tools / prompts / schemas / helpers.

**Architecture:** A `RecommendationBackend` Strategy seam at the `run_recommendation` entrypoint. `ADKBackend` wraps the existing pipeline verbatim (default). `ClaudeBackend` is explicit Python sequencing of Claude Agent SDK `query()` calls (Analyst → candidates → Explorer-with-WebSearch → enrich → Critic → log), exposing the same in-process MCP tools via `create_sdk_mcp_server`.

**Tech Stack:** Python 3.11, Google ADK 2.1.0 (existing), `claude-agent-sdk` (new, optional extra), Pydantic, SQLAlchemy/pgvector, pytest (`db_integration`/`api_dependent`).

**Spec:** `docs/superpowers/specs/2026-05-31-pluggable-agent-backend-design.md`

**Branch:** `spec/pluggable-agent-backend` (already checked out).

---

## Key facts for the implementer

- **Run in the container:** `docker exec agentic_librarian_app sh -lc 'cd /app && <cmd>'`. Postgres reachable; `db_integration` runs.
- **Lint via the commit hook (pre-commit), NOT bare `ruff check`** (the editable install makes bare ruff emit a FALSE I001 on `agentic_librarian` import order; pre-commit is authoritative — just commit; if the hook reorders imports and aborts, re-`git add` and re-commit). Commit with `SKIP=pytest git commit`; trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Do NOT push (controller batches).
- **The `claude-agent-sdk` API is newish — verify before building.** Tasks 6–7 begin with a probe of the *installed* SDK (`dir()` / field inspection) and adapt to the real API if it differs from the snippets below (the same discipline that caught ADK's `output_schema`/built-in-tool and `state_delta` gotchas).
- **Two distinct Claude dependencies:** (a) the `claude-agent-sdk` *Python package* — needed for imports + mocked offline tests; (b) the `claude` *CLI binary*, authenticated — needed only for REAL Max-quota calls (the `api_dependent` e2e). Offline tests mock `query()` and need only (a).
- Default `AGENT_BACKEND` is `adk`; **all existing tests must stay green** — the ADK path is the regression guard.

---

## Task 1: Move pure helpers to a backend-neutral module

`coerce_schema_value`, `extract_candidate_ids`, `extract_discovery_pairs` live in
`agents/pipeline.py` (which imports ADK). The Claude backend must reuse them without importing ADK.

**Files:**
- Create: `src/agentic_librarian/agents/candidates.py`
- Modify: `src/agentic_librarian/agents/pipeline.py`
- Test: `test/unit/test_pipeline_agents.py` (existing — must still pass via re-export)

- [ ] **Step 1: Create `candidates.py` with the three helpers moved verbatim**

`src/agentic_librarian/agents/candidates.py`:

```python
"""Backend-neutral pure helpers for the recommendation pipeline: parse the Analyst/Explorer
structured outputs and gather internal candidate ids. No ADK / Claude imports — both backends
reuse these."""

from __future__ import annotations

import json
import re

from agentic_librarian.mcp.server import get_unacted_suggestions, search_internal_database


def coerce_schema_value(value) -> dict:
    """An LLM structured result may arrive as a dict, a JSON string (optionally fenced), or a
    Pydantic model. Normalize to a plain dict."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return {}
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
```

- [ ] **Step 2: In `pipeline.py`, delete those three function definitions and re-import them**

In `src/agentic_librarian/agents/pipeline.py`: remove the `coerce_schema_value`,
`extract_candidate_ids`, `extract_discovery_pairs` definitions and the now-unused `json`/`re`
imports and the `search_internal_database`/`get_unacted_suggestions` imports IF they are no longer
used elsewhere in the file (the agents call `enrich_and_persist_work`/`log_suggestion` — keep those).
Add at the top:

```python
from agentic_librarian.agents.candidates import (
    coerce_schema_value,  # noqa: F401  (re-exported for existing importers)
    extract_candidate_ids,
    extract_discovery_pairs,
)
```

(The `noqa` keeps `coerce_schema_value` re-exported even if `pipeline.py` itself no longer calls it,
so `test_pipeline_agents.py`'s `from ...pipeline import coerce_schema_value` keeps working.)

- [ ] **Step 3: Run the existing helper + offline tests**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_pipeline_agents.py -q && pytest -m "not api_dependent and not slow" -q 2>&1 | tail -3'`
Expected: all pass (the helpers are unchanged; only their home moved).

- [ ] **Step 4: Commit**

```
git add src/agentic_librarian/agents/candidates.py src/agentic_librarian/agents/pipeline.py
SKIP=pytest git commit -m "refactor(agents): move pure pipeline helpers to backend-neutral candidates.py"
```

---

## Task 2: Extract shared prompts

**Files:**
- Create: `src/agentic_librarian/agents/prompts.py`
- Modify: `src/agentic_librarian/agents/services.py`
- Test: `test/unit/test_prompts.py`

- [ ] **Step 1: Write the failing test**

`test/unit/test_prompts.py`:

```python
from agentic_librarian.agents import prompts


def test_prompts_are_nonempty_strings():
    for name in ("ANALYST_INSTRUCTION", "EXPLORER_INSTRUCTION", "CRITIC_INSTRUCTION"):
        value = getattr(prompts, name)
        assert isinstance(value, str) and len(value.strip()) > 50


def test_services_use_shared_prompts(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    from agentic_librarian.agents.services import create_agent_mesh

    mesh = create_agent_mesh()
    assert mesh["analyst"].instruction == prompts.ANALYST_INSTRUCTION
    assert mesh["explorer"].instruction == prompts.EXPLORER_INSTRUCTION
    assert mesh["critic"].instruction == prompts.CRITIC_INSTRUCTION
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_prompts.py -v'`
Expected: FAIL (`ModuleNotFoundError: agentic_librarian.agents.prompts`).

- [ ] **Step 3: Create `prompts.py` with the exact current instruction text**

`src/agentic_librarian/agents/prompts.py`: copy the EXACT instruction strings currently in
`services.py` (`AnalystAgent`, `ExplorerAgent`, `CriticAgent`) into module constants. Read
`services.py` to get the current text verbatim (do not paraphrase):

```python
"""Shared agent instruction text, used by both the ADK and Claude backends so the two never drift."""

ANALYST_INSTRUCTION = """<paste AnalystAgent's current instruction string verbatim>"""

EXPLORER_INSTRUCTION = """<paste ExplorerAgent's current instruction string verbatim>"""

CRITIC_INSTRUCTION = """<paste CriticAgent's current instruction string verbatim>"""
```

- [ ] **Step 4: Point `services.py` at the shared constants**

In `src/agentic_librarian/agents/services.py`: add `from agentic_librarian.agents import prompts`
and replace each agent's inline `instruction="""..."""` with `instruction=prompts.ANALYST_INSTRUCTION`
/ `prompts.EXPLORER_INSTRUCTION` / `prompts.CRITIC_INSTRUCTION`. Change nothing else (models, tools,
output_schema/output_key stay).

- [ ] **Step 5: Run the test + the agent tests**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_prompts.py test/unit/test_agent_schemas.py test/unit/test_agent_runtime.py -m "not api_dependent" -q 2>&1 | tail -4'`
Expected: pass (the instruction text is identical, just relocated).

- [ ] **Step 6: Commit**

```
git add src/agentic_librarian/agents/prompts.py src/agentic_librarian/agents/services.py test/unit/test_prompts.py
SKIP=pytest git commit -m "refactor(agents): share Analyst/Explorer/Critic prompts via prompts.py"
```

---

## Task 3: Backend protocol + factory + ADKBackend

**Files:**
- Create: `src/agentic_librarian/agents/backends/__init__.py`
- Create: `src/agentic_librarian/agents/backends/adk.py`
- Test: `test/unit/test_backends.py`

- [ ] **Step 1: Write the failing test**

`test/unit/test_backends.py`:

```python
import pytest
from agentic_librarian.agents.backends import RecommendationBackend, get_backend


def test_default_backend_is_adk(monkeypatch):
    monkeypatch.delenv("AGENT_BACKEND", raising=False)
    backend = get_backend()
    assert isinstance(backend, RecommendationBackend)
    assert backend.name == "adk"


def test_explicit_adk_backend(monkeypatch):
    monkeypatch.setenv("AGENT_BACKEND", "adk")
    assert get_backend().name == "adk"


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("AGENT_BACKEND", "bogus")
    with pytest.raises(ValueError, match="Unknown AGENT_BACKEND"):
        get_backend()
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_backends.py -v'`
Expected: FAIL (`ModuleNotFoundError: agentic_librarian.agents.backends`).

- [ ] **Step 3: Create the protocol + factory**

`src/agentic_librarian/agents/backends/__init__.py`:

```python
"""Pluggable recommendation backends. AGENT_BACKEND selects the implementation; the default is
the existing ADK/Gemini backend (no behavior change). A Claude Agent SDK backend (Max-subscription
quota) is selectable with AGENT_BACKEND=claude."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class RecommendationBackend(Protocol):
    name: str

    def run_recommendation(self, prompt: str, user_id: str = "local") -> str:
        """Run the one-shot recommendation pipeline and return the recommendation text."""
        ...


def get_backend() -> RecommendationBackend:
    """Return the configured backend (AGENT_BACKEND env var; default 'adk')."""
    choice = os.environ.get("AGENT_BACKEND", "adk").strip().lower()
    if choice == "adk":
        from agentic_librarian.agents.backends.adk import ADKBackend

        return ADKBackend()
    if choice == "claude":
        from agentic_librarian.agents.backends.claude import ClaudeBackend

        return ClaudeBackend()
    raise ValueError(f"Unknown AGENT_BACKEND={choice!r} (expected 'adk' or 'claude').")
```

`src/agentic_librarian/agents/backends/adk.py`:

```python
"""ADK/Gemini recommendation backend — wraps the existing SequentialAgent pipeline."""

from __future__ import annotations

import asyncio
import uuid

from agentic_librarian.agents.pipeline import create_recommendation_pipeline
from agentic_librarian.agents.runtime import APP_NAME, _ensure_adk_credentials
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


class ADKBackend:
    name = "adk"

    def build_runner(self) -> Runner:
        _ensure_adk_credentials()
        return Runner(
            agent=create_recommendation_pipeline(),
            app_name=APP_NAME,
            session_service=InMemorySessionService(),
        )

    async def arun(self, prompt: str, user_id: str = "local") -> str:
        runner = self.build_runner()
        session_id = uuid.uuid4().hex
        await runner.session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        content = types.Content(role="user", parts=[types.Part(text=prompt)])
        async for _ in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
            pass
        session = await runner.session_service.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        return session.state.get("recommendation") or "(no recommendation)"

    def run_recommendation(self, prompt: str, user_id: str = "local") -> str:
        return asyncio.run(self.arun(prompt, user_id))
```

NOTE: `adk.py` imports `APP_NAME` and `_ensure_adk_credentials` from `runtime.py`. Task 4 makes
`runtime.run_recommendation` delegate to the backend; to avoid a circular import, `adk.py` imports
the small helpers from `runtime.py` while `runtime.py` imports `get_backend` lazily (inside the
function). If a cycle appears, move `APP_NAME` + `_ensure_adk_credentials` into a tiny
`agents/adk_common.py` and import from there in both — report if needed.

- [ ] **Step 4: Run the backend tests**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_backends.py -v'`
Expected: PASS (default + explicit adk; unknown raises). The `claude` branch isn't exercised yet.

- [ ] **Step 5: Commit**

```
git add src/agentic_librarian/agents/backends/__init__.py src/agentic_librarian/agents/backends/adk.py test/unit/test_backends.py
SKIP=pytest git commit -m "feat(agents): RecommendationBackend protocol + factory + ADKBackend"
```

---

## Task 4: Dispatch `run_recommendation` via the backend factory

**Files:**
- Modify: `src/agentic_librarian/agents/runtime.py`
- Test: `test/unit/test_runtime_pipeline.py` (existing), `test/unit/test_backend_dispatch.py` (new)

- [ ] **Step 1: Write the failing dispatch test**

`test/unit/test_backend_dispatch.py`:

```python
from agentic_librarian.agents import runtime


class _FakeBackend:
    name = "fake"

    def run_recommendation(self, prompt, user_id="local"):
        return f"FAKE[{prompt}]"


def test_run_recommendation_delegates_to_backend(monkeypatch):
    monkeypatch.setattr(runtime, "get_backend", lambda: _FakeBackend())
    assert runtime.run_recommendation("grim") == "FAKE[grim]"
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_backend_dispatch.py -v'`
Expected: FAIL (`runtime` has no attribute `get_backend`, or it ignores the patched backend).

- [ ] **Step 3: Make `runtime.run_recommendation` delegate**

In `src/agentic_librarian/agents/runtime.py`:
- Add a lazy import + module-level reference so it is patchable:

```python
from agentic_librarian.agents.backends import get_backend  # noqa: E402  (placed after APP_NAME etc.)
```

- Replace `run_recommendation` / `arun_recommendation` / `build_pipeline_runner` bodies with delegation:

```python
def run_recommendation(prompt: str, user_id: str = "local") -> str:
    """One-shot recommendation via the configured backend (AGENT_BACKEND)."""
    return get_backend().run_recommendation(prompt, user_id)
```

Remove the old `arun_recommendation` and `build_pipeline_runner` from `runtime.py` (their logic now
lives in `ADKBackend`). KEEP `APP_NAME`, `_ensure_adk_credentials`, `build_runner`,
`LibrarianConversation`, `astart_conversation`, `start_conversation` (the conversational path is
unchanged and ADK-only). `ADKBackend` imports `APP_NAME`/`_ensure_adk_credentials` from here.

- [ ] **Step 4: Update the existing pipeline-runtime test**

`test/unit/test_runtime_pipeline.py` patched `runtime.build_pipeline_runner`, which is now gone.
Update it to patch the backend instead (mirror `test_backend_dispatch.py`), or delete it as
superseded by `test_backend_dispatch.py` + the ADKBackend's own coverage. Keep the
conversational-path tests in `test_agent_runtime.py` untouched.

- [ ] **Step 5: Run the suite (ADK behavior preserved)**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest -m "not api_dependent and not slow" -q 2>&1 | tail -3'`
Expected: all pass. The default backend is adk, so `run_recommendation` behaves exactly as before.

- [ ] **Step 6: Commit**

```
git add src/agentic_librarian/agents/runtime.py test/unit/test_runtime_pipeline.py test/unit/test_backend_dispatch.py
SKIP=pytest git commit -m "feat(runtime): run_recommendation dispatches via the backend factory"
```

---

## Task 5: Add `claude-agent-sdk` dependency + install it

**Files:**
- Modify: `pyproject.toml`
- Modify: the devcontainer/Dockerfile (install path)

- [ ] **Step 1: Add an optional `claude` extra to `pyproject.toml`**

Under `[project.optional-dependencies]`, add (verify the exact distribution name on PyPI first —
`pip index versions claude-agent-sdk`):

```toml
claude = [
    "claude-agent-sdk>=0.1.0",
]
```

- [ ] **Step 2: Install it into the running container (so imports + mocked tests work)**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pip install -e ".[claude]" 2>&1 | tail -5 && python -c "import claude_agent_sdk; print(\"claude_agent_sdk\", getattr(claude_agent_sdk, \"__version__\", \"?\"))"'`
Expected: installs and imports. If the import name differs (e.g. `claude_code_sdk`), note it and use
the correct one consistently in Tasks 6–7. If the package needs the `claude` CLI binary at import
time and it is absent, report it — imports for mocked tests should NOT require the CLI.

- [ ] **Step 3: Wire it into the devcontainer image**

In the devcontainer Dockerfile (or `postCreateCommand`), add the `claude-agent-sdk` install (e.g.
`pip install -e ".[dev,claude]"`) and install the **`claude` CLI** (per Anthropic's published
install method). Add a comment that the CLI must be authenticated in-container (manual one-time
`claude` login) for the `api_dependent` Claude e2e to run on Max quota. Do NOT block the build on
CLI auth.

- [ ] **Step 4: Commit**

```
git add pyproject.toml .devcontainer/  # adjust path to the actual devcontainer files
SKIP=pytest git commit -m "build: add claude-agent-sdk optional extra + devcontainer claude CLI"
```

---

## Task 6: Claude tool adapter (shared MCP tools as an in-process SDK MCP server)

**Files:**
- Create: `src/agentic_librarian/agents/backends/claude_tools.py`
- Test: `test/unit/test_claude_tools.py`

- [ ] **Step 1: PROBE the installed SDK's tool API**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -c "import claude_agent_sdk as c; print([n for n in dir(c) if not n.startswith(\"_\")])"'`
Confirm `create_sdk_mcp_server` and `tool` exist (names may differ slightly). Adapt Step 3 to the
real names if needed; report any major divergence from the snippet.

- [ ] **Step 2: Write the failing test**

`test/unit/test_claude_tools.py`:

```python
def test_build_librarian_mcp_server_exposes_tools():
    from agentic_librarian.agents.backends.claude_tools import build_librarian_mcp_server, LIBRARIAN_TOOL_NAMES

    server = build_librarian_mcp_server()
    assert server is not None
    # The tools the recommendation pipeline needs are exposed under the mcp__librarian__ prefix.
    for short in ("search_internal_database", "get_work_details", "get_user_trope_preferences", "log_suggestion"):
        assert f"mcp__librarian__{short}" in LIBRARIAN_TOOL_NAMES
```

- [ ] **Step 3: Implement the adapter wrapping the existing MCP functions**

`src/agentic_librarian/agents/backends/claude_tools.py`:

```python
"""Expose the existing MCP tool functions to the Claude Agent SDK as an in-process SDK MCP server,
so the Claude backend reuses the SAME tool logic (and set_db_manager test injection) as ADK."""

from __future__ import annotations

from typing import Any

from agentic_librarian.mcp import server as mcp_server
from claude_agent_sdk import create_sdk_mcp_server, tool

_SERVER_NAME = "librarian"

# (short_name, description, python_callable). These are the existing mcp/server.py functions.
_TOOLS = [
    ("get_user_trope_preferences", "Aggregate the user's frequent tropes.", mcp_server.get_user_trope_preferences),
    ("search_internal_database", "Vector search the catalog by tropes/styles.", mcp_server.search_internal_database),
    ("get_unacted_suggestions", "Prior unread suggestions ranked by vibe.", mcp_server.get_unacted_suggestions),
    ("get_work_details", "Deep metadata + tropes + styles for a work id.", mcp_server.get_work_details),
    ("check_reading_history", "Read status + re-read eligibility for a title.", mcp_server.check_reading_history),
    ("log_suggestion", "Log a recommendation to the Suggestions table.", mcp_server.log_suggestion),
]

LIBRARIAN_TOOL_NAMES = [f"mcp__{_SERVER_NAME}__{short}" for short, _, _ in _TOOLS]


def _wrap(short: str, description: str, fn):
    # The SDK @tool decorator wraps an async handler that returns {"content": [...]}; we adapt the
    # sync DB function and return its result as text the model reads.
    @tool(short, description, {"type": "object", "properties": {}, "additionalProperties": True})
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        import json

        result = await asyncio.to_thread(lambda: fn(**args))  # don't block the loop
        return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}

    return _handler


def build_librarian_mcp_server():
    """Build the in-process SDK MCP server exposing the librarian DB tools."""
    return create_sdk_mcp_server(
        name=_SERVER_NAME,
        version="1.0.0",
        tools=[_wrap(short, desc, fn) for short, desc, fn in _TOOLS],
    )
```

If the probe (Step 1) shows the `@tool` schema arg or handler signature differs, adapt to the real
API — the intent is: one SDK MCP server named `librarian` exposing these six functions, with each
handler calling the existing sync function (off-thread) and returning its JSON as text.

- [ ] **Step 4: Run the test**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_claude_tools.py -v'`
Expected: PASS (the server builds; the tool names are present).

- [ ] **Step 5: Commit**

```
git add src/agentic_librarian/agents/backends/claude_tools.py test/unit/test_claude_tools.py
SKIP=pytest git commit -m "feat(claude): in-process SDK MCP server wrapping the librarian tools"
```

---

## Task 7: ClaudeBackend — the one-shot pipeline

**Files:**
- Create: `src/agentic_librarian/agents/backends/claude.py`
- Test: `test/unit/test_claude_backend.py`

- [ ] **Step 1: PROBE the query/options/result API**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -c "import claude_agent_sdk as c; print([n for n in dir(c) if not n.startswith(\"_\")]); from claude_agent_sdk import ClaudeAgentOptions; print([f for f in dir(ClaudeAgentOptions) if not f.startswith(\"_\")])"'`
Confirm `query`, `ClaudeAgentOptions`, `ResultMessage`, and the option fields (`system_prompt`,
`allowed_tools`, `mcp_servers`, `max_turns`, `model`, and a structured-output/`output_format`
field). Adapt the code below to the real fields. If structured output is NOT a supported option,
fall back to instructing JSON-as-text and parsing with `coerce_schema_value` (the helper already
strips fences) — same pattern as the ADK Explorer.

- [ ] **Step 2: Write the failing test (mock `query`)**

`test/unit/test_claude_backend.py`:

```python
from unittest.mock import patch

from agentic_librarian.agents.backends.claude import ClaudeBackend


class _Result:
    def __init__(self, result=None, structured_output=None):
        self.result = result
        self.structured_output = structured_output


def _fake_query_factory(scripted):
    """Return an async-generator function that yields the next scripted message list per call."""
    calls = {"i": 0}

    async def fake_query(prompt, options=None):
        msgs = scripted[calls["i"]]
        calls["i"] += 1
        for m in msgs:
            yield m

    return fake_query, calls


def test_claude_backend_sequences_pipeline_and_returns_recommendation(monkeypatch):
    # Analyst -> targets; Explorer -> discoveries; Critic -> recommendation text.
    scripted = [
        [_Result(structured_output={"tropes": ["heist"], "styles": [], "session_constraints": []})],  # Analyst
        [_Result(structured_output={"books": []})],  # Explorer (no discoveries -> no enrichment)
        [_Result(result="I recommend The Long War because it features grimdark war.")],  # Critic
    ]
    fake_query, calls = _fake_query_factory(scripted)

    with patch("agentic_librarian.agents.backends.claude.query", fake_query), patch(
        "agentic_librarian.agents.backends.claude.extract_candidate_ids", return_value=["w1"]
    ), patch("agentic_librarian.agents.backends.claude.log_suggestion") as mock_log:
        out = ClaudeBackend().run_recommendation("a heist book")

    assert "recommend" in out.lower()
    assert calls["i"] == 3  # Analyst, Explorer, Critic each queried once
    mock_log.assert_called_once()  # Logger logged the top candidate
```

- [ ] **Step 3: Implement `ClaudeBackend`**

`src/agentic_librarian/agents/backends/claude.py`:

```python
"""Claude Agent SDK recommendation backend (Max-subscription quota). Explicit Python sequencing of
query() calls sharing the in-process librarian MCP tools; embeddings still go through Gemini via the
DB tools (out of scope to change)."""

from __future__ import annotations

import asyncio
import os

from agentic_librarian.agents import prompts
from agentic_librarian.agents.backends.claude_tools import LIBRARIAN_TOOL_NAMES, build_librarian_mcp_server
from agentic_librarian.agents.candidates import coerce_schema_value, extract_candidate_ids, extract_discovery_pairs
from agentic_librarian.agents.schemas import Discoveries, Targets
from agentic_librarian.mcp.server import enrich_and_persist_work, log_suggestion
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query


def _model() -> str:
    return os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")


async def _ask(prompt: str, *, system: str, allowed_tools: list[str], schema=None) -> object:
    """Run one query() turn; return structured_output (if a schema was requested) or the result text."""
    options = ClaudeAgentOptions(
        system_prompt=system,
        model=_model(),
        mcp_servers={"librarian": build_librarian_mcp_server()},
        allowed_tools=allowed_tools,
    )
    if schema is not None:
        # Adapt to the probed API: set the structured-output option on `options`.
        options.output_format = {"type": "json_schema", "schema": schema.model_json_schema()}

    structured, text = None, ""
    async for message in query(prompt=prompt, options=options):
        if getattr(message, "structured_output", None) is not None:
            structured = message.structured_output
        if isinstance(message, ResultMessage) and getattr(message, "result", None):
            text = message.result
    return structured if schema is not None else text


async def _arun(prompt: str) -> str:
    state: dict = {}

    # 1. Analyst -> targets
    state["targets"] = await _ask(
        prompt, system=prompts.ANALYST_INSTRUCTION,
        allowed_tools=["mcp__librarian__get_user_trope_preferences"], schema=Targets,
    )

    # 2. Internal candidates (pure Python; shared helper)
    candidate_ids = extract_candidate_ids(state)

    # 3. Explorer -> discoveries (Claude WebSearch built-in tool)
    state["discoveries"] = await _ask(
        f"{prompt}\nTargets: {coerce_schema_value(state['targets'])}",
        system=prompts.EXPLORER_INSTRUCTION, allowed_tools=["WebSearch"], schema=Discoveries,
    )

    # 4. Enrichment (pure Python; shared tool)
    for title, author in extract_discovery_pairs(state):
        wid = await asyncio.to_thread(enrich_and_persist_work, title, author)
        if wid and wid not in candidate_ids:
            candidate_ids.append(wid)

    # 5. Critic -> recommendation text
    critic_prompt = (
        f"Target vibes: {coerce_schema_value(state['targets'])}\nCandidate work ids: {candidate_ids}\n"
        f"User request: {prompt}"
    )
    recommendation = await _ask(
        critic_prompt, system=prompts.CRITIC_INSTRUCTION,
        allowed_tools=[
            "mcp__librarian__search_internal_database",
            "mcp__librarian__get_work_details",
            "mcp__librarian__check_reading_history",
        ],
    )
    recommendation = recommendation or "(no recommendation)"

    # 6. Logger (pure Python; shared tool)
    if recommendation != "(no recommendation)" and candidate_ids:
        await asyncio.to_thread(
            log_suggestion, work_id=candidate_ids[0], context="recommendation", justification=recommendation[:1000]
        )
    return recommendation


class ClaudeBackend:
    name = "claude"

    def run_recommendation(self, prompt: str, user_id: str = "local") -> str:
        return asyncio.run(_arun(prompt))
```

Adapt `_ask` to the probed API (the `output_format` field name, how `structured_output` surfaces,
and the `WebSearch` tool name). `LIBRARIAN_TOOL_NAMES` is imported for reference/validation; the
per-step `allowed_tools` lists are explicit subsets.

- [ ] **Step 4: Run the mocked backend test**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_claude_backend.py -v'`
Expected: PASS — the sequence runs (3 query calls), discoveries empty → no enrich, the Critic text
returns, and the top candidate is logged. If `query`/`ClaudeAgentOptions` shapes differ, fix the
implementation AND the fakes to match the real API, keeping the test's intent (3 sequenced steps,
log called once).

- [ ] **Step 5: Commit**

```
git add src/agentic_librarian/agents/backends/claude.py test/unit/test_claude_backend.py
SKIP=pytest git commit -m "feat(claude): ClaudeBackend one-shot recommendation pipeline"
```

---

## Task 8: Backend-neutral contract test + Claude live e2e

**Files:**
- Create: `test/integration/test_backend_contract.py`
- Create: `test/integration/test_claude_e2e.py`

- [ ] **Step 1: Backend-neutral contract test (runs on ADK in CI)**

`test/integration/test_backend_contract.py`:

```python
import pytest
from agentic_librarian.agents.backends import get_backend
from agentic_librarian.db.models import Suggestions
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import set_db_manager
from test.integration.seed_helpers import seed_recommendation_fixture


@pytest.mark.api_dependent
@pytest.mark.db_integration
def test_configured_backend_recommends_and_logs(db_url):
    dbm = DatabaseManager(db_url)
    set_db_manager(dbm)
    with dbm.get_session() as session:
        seed_recommendation_fixture(session)

    result = get_backend().run_recommendation("a slow-burn enemies-to-lovers romance")

    assert isinstance(result, str) and len(result.strip()) > 30
    assert result != "(no recommendation)"
    with dbm.get_session() as session:
        assert session.query(Suggestions).count() >= 1
```

(This is `api_dependent` because the default ADK backend still makes real Gemini calls; it
exercises the seam against whichever backend `AGENT_BACKEND` selects — set `AGENT_BACKEND=claude`
to run it on Claude. **CI coverage of the seam itself** is the deterministic unit tests from Tasks
3–4 — `test_backends.py` / `test_backend_dispatch.py` — which assert factory selection and
dispatch without any LLM call. This full contract test is the live, api_dependent validation.)

- [ ] **Step 2: Claude-specific live e2e**

`test/integration/test_claude_e2e.py`:

```python
import os

import pytest
from agentic_librarian.agents.backends.claude import ClaudeBackend
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import set_db_manager
from test.integration.seed_helpers import seed_recommendation_fixture


@pytest.mark.api_dependent
@pytest.mark.db_integration
@pytest.mark.skipif("claude" not in os.environ.get("CLAUDE_E2E", ""), reason="set CLAUDE_E2E=claude to run")
def test_claude_backend_live(db_url):
    dbm = DatabaseManager(db_url)
    set_db_manager(dbm)
    with dbm.get_session() as session:
        seed_recommendation_fixture(session)
    result = ClaudeBackend().run_recommendation("a slow-burn enemies-to-lovers romance")
    assert isinstance(result, str) and len(result.strip()) > 30
```

(The extra `skipif` guard means it only runs when explicitly requested AND the `claude` CLI is
authenticated — it won't fail collection/runs for others. Run it manually with
`CLAUDE_E2E=claude` + an authed `claude` CLI to validate on Max quota.)

- [ ] **Step 3: Confirm both are excluded from the offline suite**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest -m "not api_dependent and not slow" -q 2>&1 | tail -3'`
Expected: suite green; both new tests deselected (`api_dependent`).

- [ ] **Step 4: (Optional, if quota/CLI available) run the Claude live e2e**

Run: `docker exec -e CLAUDE_E2E=claude agentic_librarian_app sh -lc 'cd /app && pytest test/integration/test_claude_e2e.py -v -m "api_dependent and db_integration" 2>&1 | tail -25'`
Expected: PASS on Max quota (a justified recommendation). If the `claude` CLI isn't authed
in-container, it skips/errors clearly — report and defer to manual verification.

- [ ] **Step 5: Commit**

```
git add test/integration/test_backend_contract.py test/integration/test_claude_e2e.py
SKIP=pytest git commit -m "test(backends): backend-neutral contract test + Claude live e2e (api_dependent)"
```

---

## Task 9: ADR + docs

**Files:**
- Modify: `docs/project_notes/decisions.md`

- [ ] **Step 1: Append ADR-041**

Add to the end of `docs/project_notes/decisions.md`:

```markdown

### ADR-041: Pluggable Agent Backend (ADK + Claude Agent SDK) (2026-05-31)
**Context:**
- The ADK mesh calls models via API keys (Gemini), which cannot reach the user's Claude Max
  *subscription* quota — only the Claude Agent SDK's auto-detected Claude Code auth can. The Gemini
  free-tier 429 wall repeatedly blocked live verification.

**Decision:**
- Introduce a `RecommendationBackend` Strategy seam at the `run_recommendation` entrypoint
  (`AGENT_BACKEND=adk|claude`, default `adk`). `ADKBackend` wraps the existing pipeline verbatim;
  `ClaudeBackend` is explicit Python sequencing of Claude Agent SDK `query()` calls (Analyst →
  candidates → Explorer-with-WebSearch → enrich → Critic → log), exposing the SAME in-process MCP
  tools via `create_sdk_mcp_server`. Prompts (`agents/prompts.py`), schemas (`agents/schemas.py`),
  and pure helpers (`agents/candidates.py`) are shared so the backends never drift.
- Embeddings stay on Gemini (pgvector / `gemini-embedding-001`) for both backends — separate,
  low-volume quota. The `claude-agent-sdk` is an optional extra; the `claude` CLI is authenticated
  in-container for Max-quota calls.

**Consequences:**
- The recurring Gemini quota wall is bypassable by flipping one config value; the ADK work is
  preserved as the default backend. Two agent implementations to maintain. Subscription-quota use
  for a personal app is a ToS gray area (acceptable for personal use; not a supported product path).
  Conversational Librarian on Claude and security hardening remain out of scope.
```

- [ ] **Step 2: Commit**

```
git add docs/project_notes/decisions.md
SKIP=pytest git commit -m "docs: ADR-041 pluggable agent backend"
```

---

## Final verification (after all tasks)

- [ ] **Offline suite green (default adk backend):**
```
docker exec agentic_librarian_app sh -lc 'cd /app && pytest -m "not api_dependent and not slow" -q 2>&1 | tail -5'
```
Expected: all pass; the ADK path behaves exactly as before; the Claude tests are deselected.

- [ ] **CI-conditions (dummy keys):**
```
docker exec -e GOOGLE_SEARCH_API_KEY=dummy -e GEMINI_API_KEY=dummy -e GOOGLE_API_KEY=dummy agentic_librarian_app sh -lc 'cd /app && pytest -m "not api_dependent and not slow" -q 2>&1 | tail -5'
```
Expected: all pass (no real API in non-api_dependent tests; `claude_agent_sdk` imports but `query` is never called offline).

- [ ] **Finish the branch** via `superpowers:finishing-a-development-branch` (push + open a PR; do not self-merge).
```
