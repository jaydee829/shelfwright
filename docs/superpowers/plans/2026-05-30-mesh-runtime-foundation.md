# Mesh Runtime Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 4-agent recommendation mesh runnable as a multi-turn conversation: assign models to the agents and add `agents/runtime.py` with an ADK Runner, a `LibrarianConversation`, and a `run_recommendation` one-shot.

**Architecture:** One ADK `Runner` hosts the existing `create_agent_mesh()` Librarian (root agent); LLM-driven `AgentTool` delegation drives Analyst/Explorer/Critic (ADR-019/020). A reusable ADK session gives conversational memory (ADR-036); `InMemorySessionService` for now.

**Tech Stack:** `google-adk` (`Runner`, `InMemorySessionService`, `LlmAgent`), `google-genai` (`types.Content`/`types.Part`), pytest.

**Spec:** `docs/superpowers/specs/2026-05-30-mesh-runtime-foundation-design.md`

### Running tests & committing (this environment)
- Tests run in the dev container: `docker exec agentic_librarian_app sh -lc 'cd /app && <pytest cmd>'` (or `pytest ...` directly inside the container).
- When committing from the **host**, prefix with `SKIP=pytest` (the host lacks the project deps for the pre-commit `pytest` hook). The explicit pytest runs in each task are the real verification. End commit messages with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/agentic_librarian/agents/services.py` (modify) | Assign a `model` to each `LlmAgent`. |
| `src/agentic_librarian/agents/runtime.py` (create) | ADK Runner + credentials + `LibrarianConversation` + `start_conversation` + `run_recommendation`. |
| `test/unit/test_agent_runtime.py` (create) | Offline (mocked) unit tests + one `api_dependent` live smoke test. |
| `.env.example` (modify) | Document `GOOGLE_API_KEY`. |

---

## Task 1: Assign a model to every mesh agent

**Files:**
- Modify: `src/agentic_librarian/agents/services.py`
- Test: `test/unit/test_agent_runtime.py` (create)

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_agent_runtime.py`:

```python
import os

import pytest
from google.genai import types

from agentic_librarian.agents import runtime  # noqa: F401  (used by later tasks)
from agentic_librarian.agents.services import create_agent_mesh


@pytest.fixture(autouse=True)
def _adk_key(monkeypatch, request):
    """ADK's Gemini model reads GOOGLE_API_KEY. Set a dummy for offline tests so
    agent/runner construction never needs a real key. Live tests opt out."""
    if "api_dependent" not in request.keywords:
        monkeypatch.setenv("GOOGLE_API_KEY", "test-adk-key")


def test_all_mesh_agents_have_a_model():
    mesh = create_agent_mesh()
    for name in ("librarian", "analyst", "explorer", "critic"):
        assert mesh[name].model, f"{name} agent has no model"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py::test_all_mesh_agents_have_a_model -v'`
Expected: FAIL — agents are constructed without a `model` (empty/None). (The `runtime` import also fails until Task 2; if collection errors on that import, temporarily comment the `from agentic_librarian.agents import runtime` line, or do Task 2's file creation first. Simplest: create an empty `src/agentic_librarian/agents/runtime.py` now so the import resolves.)

- [ ] **Step 3: Add the model to each agent**

In `src/agentic_librarian/agents/services.py`, add `import os` at the top, and a helper plus a `model=` argument on all four agents:

```python
import os

from agentic_librarian.mcp.server import (
    check_reading_history,
    get_unacted_suggestions,
    get_user_trope_preferences,
    get_work_details,
    log_suggestion,
    search_internal_database,
    update_reading_status,
    update_suggestion_status,
)
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool, FunctionTool


def _model_name() -> str:
    """Generative model for the mesh agents (configurable; matches the scouts)."""
    return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
```

Then add `model=_model_name(),` as the first argument of each `super().__init__(...)` call — in `AnalystAgent`, `ExplorerAgent`, `CriticAgent`, and `LibrarianAgent`. For example, `ExplorerAgent`:

```python
class ExplorerAgent(LlmAgent):
    """The Scout. Web-based discovery using search grounding."""

    def __init__(self):
        super().__init__(
            model=_model_name(),
            name="Explorer",
            description="Discovers new books from the internet using search grounding.",
            instruction="""
            You are a book scout. Use your internal search grounding capabilities to find real books.
            If a book is found, return its title, author, and a brief description.
            Focus on discovery of titles NOT likely to be in a standard personal library.
            """,
            # Search tool is added in Spec 2 (ENV-015 part 2); model-only placeholder for now.
        )
```

Do the same (`model=_model_name(),`) for `AnalystAgent`, `CriticAgent`, and `LibrarianAgent`, keeping their existing `tools=[...]` arguments.

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py::test_all_mesh_agents_have_a_model -v'`
Expected: PASS.

- [ ] **Step 5: Run the existing agent tests to check for regressions**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_services.py -q'`
Expected: PASS (adding a model string must not break existing construction tests).

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/agents/services.py test/unit/test_agent_runtime.py
SKIP=pytest git commit -m "feat(agents): assign GEMINI_MODEL to mesh agents"
```

---

## Task 2: ADK credentials + `build_runner`

**Files:**
- Create: `src/agentic_librarian/agents/runtime.py`
- Test: `test/unit/test_agent_runtime.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/unit/test_agent_runtime.py`:

```python
def test_ensure_adk_credentials_falls_back(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "fallback-key-123")
    runtime._ensure_adk_credentials()
    assert os.environ["GOOGLE_API_KEY"] == "fallback-key-123"


def test_build_runner_constructs():
    r = runtime.build_runner()
    assert r is not None
    assert r.app_name == runtime.APP_NAME
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py -k "credentials or build_runner" -v'`
Expected: FAIL — `runtime` has no `_ensure_adk_credentials`/`build_runner`/`APP_NAME` (AttributeError).

- [ ] **Step 3: Implement `runtime.py` (this part)**

Create `src/agentic_librarian/agents/runtime.py`:

```python
"""Runtime for the recommendation agent mesh: host the Librarian in an ADK Runner
and expose a multi-turn conversation API (ADR-035 Spec 1, ADR-036)."""

import asyncio
import os
import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agentic_librarian.agents.services import create_agent_mesh

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

APP_NAME = "agentic_librarian"


def _ensure_adk_credentials() -> None:
    """ADK's Gemini model authenticates via GOOGLE_API_KEY. Populate it from the
    project's existing keys if it isn't set (GOOGLE_SEARCH_API_KEY has access to both
    Custom Search and the Gemini API in this GCP project)."""
    if not os.environ.get("GOOGLE_API_KEY"):
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if key:
            os.environ["GOOGLE_API_KEY"] = key


def build_runner() -> Runner:
    """Build a Runner hosting the Librarian (root of the agent mesh)."""
    _ensure_adk_credentials()
    mesh = create_agent_mesh()
    return Runner(
        agent=mesh["librarian"],
        app_name=APP_NAME,
        session_service=InMemorySessionService(),
    )
```

(If you created an empty `runtime.py` in Task 1, replace its contents with the above.)

- [ ] **Step 4: Run to verify pass**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py -k "credentials or build_runner" -v'`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/agents/runtime.py test/unit/test_agent_runtime.py
SKIP=pytest git commit -m "feat(agents): ADK runner + credential reconciliation"
```

---

## Task 3: `LibrarianConversation` + `start_conversation` (multi-turn)

**Files:**
- Modify: `src/agentic_librarian/agents/runtime.py`
- Test: `test/unit/test_agent_runtime.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/unit/test_agent_runtime.py` (the fakes let us test the conversation logic with no API/DB):

```python
class _FakeEvent:
    def __init__(self, text: str):
        self.content = types.Content(role="model", parts=[types.Part(text=text)])

    def is_final_response(self) -> bool:
        return True


class _FakeSessionService:
    def __init__(self):
        self.created = []

    async def create_session(self, app_name, user_id, session_id):
        self.created.append((app_name, user_id, session_id))
        return None


class _FakeRunner:
    def __init__(self, reply="Recommended: Dune"):
        self.app_name = runtime.APP_NAME
        self.session_service = _FakeSessionService()
        self.calls = []
        self._reply = reply

    async def run_async(self, user_id, session_id, new_message):
        self.calls.append((user_id, session_id, new_message.parts[0].text))
        yield _FakeEvent(self._reply)


def test_send_returns_final_response_text():
    conv = runtime.LibrarianConversation(_FakeRunner(reply="Try Hyperion"), "u", "s")
    assert conv.send("recommend sci-fi") == "Try Hyperion"


def test_two_sends_reuse_the_same_session():
    fake = _FakeRunner()
    conv = runtime.LibrarianConversation(fake, "u", "sess-1")
    conv.send("first")
    conv.send("second")
    assert [sid for (_, sid, _) in fake.calls] == ["sess-1", "sess-1"]
    assert [msg for (_, _, msg) in fake.calls] == ["first", "second"]


def test_start_conversation_creates_a_session():
    fake = _FakeRunner()
    conv = runtime.start_conversation(user_id="alice", runner=fake)
    assert conv.user_id == "alice"
    assert fake.session_service.created  # a session was created
    assert fake.session_service.created[0][1] == "alice"
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py -k "send or start_conversation" -v'`
Expected: FAIL — `runtime` has no `LibrarianConversation`/`start_conversation`.

- [ ] **Step 3: Implement the conversation API**

Append to `src/agentic_librarian/agents/runtime.py`:

```python
class LibrarianConversation:
    """A multi-turn conversation with the Librarian. Reusing one session across
    sends is what gives the agent conversational memory (ADR-036)."""

    def __init__(self, runner: Runner, user_id: str, session_id: str):
        self._runner = runner
        self.user_id = user_id
        self.session_id = session_id

    async def asend(self, message: str) -> str:
        content = types.Content(role="user", parts=[types.Part(text=message)])
        final = ""
        async for event in self._runner.run_async(
            user_id=self.user_id, session_id=self.session_id, new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final = event.content.parts[0].text or final
        return final or "(no response)"

    def send(self, message: str) -> str:
        return asyncio.run(self.asend(message))


async def astart_conversation(user_id: str = "local", runner: Runner | None = None) -> LibrarianConversation:
    runner = runner or build_runner()
    session_id = uuid.uuid4().hex
    await runner.session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    return LibrarianConversation(runner, user_id, session_id)


def start_conversation(user_id: str = "local", runner: Runner | None = None) -> LibrarianConversation:
    return asyncio.run(astart_conversation(user_id=user_id, runner=runner))
```

- [ ] **Step 4: Run to verify pass**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py -k "send or start_conversation" -v'`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/agents/runtime.py test/unit/test_agent_runtime.py
SKIP=pytest git commit -m "feat(agents): multi-turn LibrarianConversation"
```

---

## Task 4: `run_recommendation` one-shot

**Files:**
- Modify: `src/agentic_librarian/agents/runtime.py`
- Test: `test/unit/test_agent_runtime.py`

- [ ] **Step 1: Write the failing test**

Append to `test/unit/test_agent_runtime.py`:

```python
def test_run_recommendation_one_shot(monkeypatch):
    fake = _FakeRunner(reply="Recommended: Dune")
    monkeypatch.setattr(runtime, "build_runner", lambda: fake)
    assert runtime.run_recommendation("something like Dune") == "Recommended: Dune"
    assert fake.calls[0][2] == "something like Dune"
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py::test_run_recommendation_one_shot -v'`
Expected: FAIL — `runtime` has no `run_recommendation`.

- [ ] **Step 3: Implement `run_recommendation`**

Append to `src/agentic_librarian/agents/runtime.py`:

```python
def run_recommendation(prompt: str, user_id: str = "local") -> str:
    """One-shot convenience: start a conversation and send a single message."""

    async def _once() -> str:
        conv = await astart_conversation(user_id=user_id)
        return await conv.asend(prompt)

    return asyncio.run(_once())
```

- [ ] **Step 4: Run to verify pass**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py::test_run_recommendation_one_shot -v'`
Expected: PASS.

- [ ] **Step 5: Run the full unit file**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py -q'`
Expected: PASS (all offline tests; the `api_dependent` test from Task 5 is deselected by default).

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/agents/runtime.py test/unit/test_agent_runtime.py
SKIP=pytest git commit -m "feat(agents): run_recommendation one-shot entrypoint"
```

---

## Task 5: Document `GOOGLE_API_KEY` + live smoke test

**Files:**
- Modify: `.env.example`
- Test: `test/unit/test_agent_runtime.py`

- [ ] **Step 1: Document the key**

In `.env.example`, under the Google APIs section (after `GOOGLE_BOOKS_API_KEY`), add:

```bash
# ADK (recommendation mesh) authenticates Gemini via GOOGLE_API_KEY. Optional if
# GOOGLE_SEARCH_API_KEY is set (the runtime falls back to it).
# GOOGLE_API_KEY=
```

- [ ] **Step 2: Add the live smoke test (api_dependent — not run in CI)**

Append to `test/unit/test_agent_runtime.py`:

```python
@pytest.mark.api_dependent
def test_live_conversation_runs():
    conv = runtime.start_conversation()
    first = conv.send("Recommend a sci-fi novel like Dune in one sentence.")
    assert isinstance(first, str) and first.strip()
    # Second turn shares the session (memory).
    second = conv.send("Actually, something more recent.")
    assert isinstance(second, str) and second.strip()
```

- [ ] **Step 3: Confirm it is deselected by the CI marker filter**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py -m "not api_dependent and not slow" -q'`
Expected: PASS, with `test_live_conversation_runs` deselected.

- [ ] **Step 4: Commit**

```bash
git add .env.example test/unit/test_agent_runtime.py
SKIP=pytest git commit -m "docs(env): document GOOGLE_API_KEY; add live mesh smoke test"
```

---

## Task 6: Full-suite regression check

- [ ] **Step 1: Run the whole CI subset**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest -m "not api_dependent and not slow" -q'`
Expected: PASS, with a higher count than before (the new offline runtime tests added; the one `api_dependent` test deselected). No regressions.

- [ ] **Step 2 (optional, with API quota): run the live smoke**

Run only when you have a working key + DB and accept the API cost:
`docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py::test_live_conversation_runs -v'`
Expected: PASS — the Librarian runs and returns text for two turns. (On an empty/seed DB the recommendation content will be thin; that's expected — quality is Specs 2–4.)

---

## Self-Review

**Spec coverage:**
- Models on all agents → Task 1. ✓
- `agents/runtime.py` with `build_runner`, `LibrarianConversation` (`asend`/`send`), `start_conversation`, `run_recommendation` → Tasks 2–4. ✓
- Auth reconciliation (`GOOGLE_API_KEY` ← `GEMINI_API_KEY`/`GOOGLE_SEARCH_API_KEY`) → Task 2. ✓
- `.env.example` documents `GOOGLE_API_KEY` → Task 5. ✓
- Mock unit tests (runner constructs; agents have models; conversation returns final text; two sends reuse session; one-shot) → Tasks 1–4. ✓
- `api_dependent` live smoke (incl. two-turn) → Task 5. ✓
- In-memory session, multi-turn memory via reused `session_id` → Task 3 (verified by `test_two_sends_reuse_the_same_session`). ✓

**Placeholder scan:** No TBDs; every code step shows the full code. ✓

**Type/name consistency:** `APP_NAME`, `_ensure_adk_credentials`, `build_runner`, `LibrarianConversation(.asend/.send)`, `astart_conversation`, `start_conversation`, `run_recommendation`, `_model_name`, `_FakeRunner/_FakeEvent/_FakeSessionService` are used consistently across tasks. ✓

**Note for the implementer:** If ADK `LlmAgent` construction unexpectedly requires a real key (it shouldn't — model strings resolve lazily, and the existing `test_agent_services.py` constructs agents offline), the `_adk_key` autouse fixture already sets a dummy `GOOGLE_API_KEY` for non-`api_dependent` tests.
