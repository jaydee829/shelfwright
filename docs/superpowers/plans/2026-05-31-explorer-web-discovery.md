# Explorer Web Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `ExplorerAgent` grounded web search via ADK's `GoogleSearchTool(bypass_multi_tools_limit=True)` so the Librarian→Explorer flow discovers real (incl. recent) books.

**Architecture:** The Explorer is a sub-agent of the Librarian (`AgentTool`). ADK forbids *built-in* tools in sub-agents, but `bypass_multi_tools_limit=True` converts `google_search` into a function-calling tool that works there (spike-verified on ADK 2.1.0, ADR-037). The Explorer gets its own stronger model and a tightened, anti-hallucination instruction.

**Tech Stack:** `google-adk` 2.1.0 (`GoogleSearchTool`, `LlmAgent`, `AgentTool`), the Spec 1 runtime (`agents/runtime.py`), pytest.

**Spec:** `docs/superpowers/specs/2026-05-31-explorer-web-discovery-design.md`

### Running tests & committing (this environment)
- Tests run in the dev container: `docker exec agentic_librarian_app sh -lc 'cd /app && <pytest cmd>'`.
- When committing from the **host**, prefix with `SKIP=pytest` (the host lacks project deps for the pre-commit `pytest` hook). The explicit pytest runs in each task are the verification. If a commit fails because ruff/ruff-format modified files, re-`git add` and re-commit. Ignore CRLF/LF git warnings. End commit messages with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/agentic_librarian/agents/services.py` (modify) | Explorer gets `EXPLORER_MODEL` + `GoogleSearchTool` + tightened instruction. |
| `.env.example` (modify) | Document `EXPLORER_MODEL`. |
| `test/unit/test_agent_runtime.py` (modify) | Mock config tests + one `api_dependent` live delegation test (reuses Spec 1's `_adk_key` fixture and the runtime). |

---

## Task 1: Explorer uses its own `EXPLORER_MODEL`

**Files:**
- Modify: `src/agentic_librarian/agents/services.py`
- Test: `test/unit/test_agent_runtime.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/unit/test_agent_runtime.py`:

```python
def test_explorer_uses_explorer_model_env(monkeypatch):
    monkeypatch.setenv("EXPLORER_MODEL", "gemini-test-explorer")
    mesh = create_agent_mesh()
    assert mesh["explorer"].model == "gemini-test-explorer"


def test_explorer_model_defaults_to_flash(monkeypatch):
    monkeypatch.delenv("EXPLORER_MODEL", raising=False)
    mesh = create_agent_mesh()
    assert mesh["explorer"].model == "gemini-2.5-flash"
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py -k explorer_model -v'`
Expected: FAIL — the Explorer currently uses `_model_name()` (flash-lite), not `gemini-2.5-flash`.

- [ ] **Step 3: Add the `_explorer_model` helper and use it**

In `src/agentic_librarian/agents/services.py`, add this helper next to the existing `_model_name()`:

```python
def _explorer_model() -> str:
    """The Explorer does grounded web discovery, which benefits from a stronger model
    than the flash-lite default used by the other agents."""
    return os.environ.get("EXPLORER_MODEL", "gemini-2.5-flash")
```

Then in `ExplorerAgent.__init__`, change `model=_model_name(),` to `model=_explorer_model(),`.

- [ ] **Step 4: Run to verify pass**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py -k explorer_model -v'`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/agents/services.py test/unit/test_agent_runtime.py
SKIP=pytest git commit -m "feat(agents): Explorer uses its own EXPLORER_MODEL (gemini-2.5-flash)"
```

---

## Task 2: Explorer gets the grounded `google_search` tool

**Files:**
- Modify: `src/agentic_librarian/agents/services.py`
- Test: `test/unit/test_agent_runtime.py`

- [ ] **Step 1: Write the failing test**

Append to `test/unit/test_agent_runtime.py`:

```python
def test_explorer_has_a_google_search_tool():
    mesh = create_agent_mesh()
    tool_types = [type(t).__name__ for t in mesh["explorer"].tools]
    assert any("GoogleSearch" in name for name in tool_types), tool_types
```

- [ ] **Step 2: Run to verify failure**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py::test_explorer_has_a_google_search_tool -v'`
Expected: FAIL — the Explorer has no tools (likely `AttributeError`/empty `tools`).

- [ ] **Step 3: Add the tool + tighten the instruction**

In `src/agentic_librarian/agents/services.py`:

1. Add the import near the other ADK imports:
```python
from google.adk.tools.google_search_tool import GoogleSearchTool
```

2. Replace the whole `ExplorerAgent` class with:
```python
class ExplorerAgent(LlmAgent):
    """The Scout. External web discovery via grounded search (ADR-035)."""

    def __init__(self):
        super().__init__(
            model=_explorer_model(),
            name="Explorer",
            description="Discovers new/recent books from the web using grounded search.",
            instruction="""
            You are a book scout. Use the google_search tool to find REAL books that
            match the user's request. Prefer recent or lesser-known titles that are
            unlikely to already be in a standard personal library.

            For each book give: Title — Author — one short sentence on why it fits.
            Return a handful (3-5).

            CRITICAL: Only report books that appear in your search results. Never invent
            titles, authors, or details. If the search finds nothing relevant, say so.
            """,
            tools=[GoogleSearchTool(bypass_multi_tools_limit=True)],
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py::test_explorer_has_a_google_search_tool -v'`
Expected: PASS.

- [ ] **Step 5: Run the full mesh/runtime tests for regressions**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py test/unit/test_agent_services.py -m "not api_dependent" -q'`
Expected: PASS — adding the Explorer's tool must not break mesh construction (`build_runner`, `create_agent_mesh`) or the Spec 1 tests.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/agents/services.py test/unit/test_agent_runtime.py
SKIP=pytest git commit -m "feat(agents): Explorer grounded web search via GoogleSearchTool(bypass_multi_tools_limit)"
```

---

## Task 3: Document `EXPLORER_MODEL`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add the variable**

In `.env.example`, find the `GEMINI_MODEL=` line (in the Google APIs / LLM section) and add after it:

```bash
# Model for the Explorer agent's grounded web discovery (benefits from a stronger
# model than the flash-lite default). Defaults to gemini-2.5-flash if unset.
EXPLORER_MODEL=gemini-2.5-flash
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
SKIP=pytest git commit -m "docs(env): document EXPLORER_MODEL"
```

---

## Task 4: Live delegation smoke test (`api_dependent`)

**Files:**
- Test: `test/unit/test_agent_runtime.py`

- [ ] **Step 1: Add the live test**

Append to `test/unit/test_agent_runtime.py`:

```python
@pytest.mark.api_dependent
def test_librarian_delegates_discovery_to_explorer():
    # Asks for a recent release the model cannot know without searching, so a
    # substantive answer implies the grounded Explorer ran. (Strict grounding
    # correctness is a manual check — results vary.)
    response = runtime.run_recommendation(
        "Find me a grimdark fantasy novel published in 2024 that I probably haven't read."
    )
    assert isinstance(response, str)
    assert len(response.strip()) > 30
```

- [ ] **Step 2: Confirm it is deselected by the CI marker filter (do NOT run the live test)**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py -m "not api_dependent and not slow" -q'`
Expected: PASS, with at least 2 `deselected` (the Spec 1 live test + this one).

- [ ] **Step 3: Commit**

```bash
git add test/unit/test_agent_runtime.py
SKIP=pytest git commit -m "test(agents): api_dependent live Librarian->Explorer discovery smoke"
```

---

## Task 5: Full-suite regression + optional live run

- [ ] **Step 1: Full CI subset**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pytest -m "not api_dependent and not slow" -q'`
Expected: PASS, count up by the new mock tests; no regressions.

- [ ] **Step 2 (optional, uses API quota): run the live discovery test**

Run only with a working key + the mesh up, accepting API cost:
`docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_agent_runtime.py::test_librarian_delegates_discovery_to_explorer -v'`
Expected: PASS, and a manual glance at the captured output should show real, recent (2024) titles — confirming the grounded discovery ran (not hallucinated recall).

---

## Self-Review

**Spec coverage:**
- Explorer `EXPLORER_MODEL` (gemini-2.5-flash); other agents unchanged → Task 1. ✓
- Explorer `GoogleSearchTool(bypass_multi_tools_limit=True)` + tightened anti-hallucination instruction → Task 2. ✓
- `.env.example` documents `EXPLORER_MODEL` → Task 3. ✓
- Mock unit: Explorer configured with a GoogleSearch tool + EXPLORER_MODEL → Tasks 1–2. ✓
- `api_dependent` live Librarian→Explorer discovery → Task 4. ✓
- `search_strategies.py` left untouched → not modified by any task. ✓
- Out-of-scope items (ranking/dedup/persistence/citations) → no tasks, as intended. ✓

**Placeholder scan:** No TBDs; every code step shows full code. ✓

**Type/name consistency:** `_explorer_model`, `EXPLORER_MODEL`, `GoogleSearchTool(bypass_multi_tools_limit=True)`, `create_agent_mesh()["explorer"]`, `runtime.run_recommendation` are used consistently and match Spec 1's existing names. ✓

**Note for the implementer:** The `_adk_key` autouse fixture (from Spec 1, already in `test_agent_runtime.py`) sets a dummy `GOOGLE_API_KEY` for non-`api_dependent` tests, so `create_agent_mesh()` construction stays offline. If `test_explorer_has_a_google_search_tool` doesn't match (ADK wraps the tool differently than expected), inspect `type(t).__name__` for the Explorer's tools and adjust the substring — do not weaken it to always-true.
