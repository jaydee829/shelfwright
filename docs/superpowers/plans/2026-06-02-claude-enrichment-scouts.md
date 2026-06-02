# Claude-Native Enrichment Scouts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the enrichment LLM scouts backend-selectable under the single `AGENT_BACKEND` knob, so `AGENT_BACKEND=claude` runs them (recommendation pipeline AND Flow-1 ETL) on the Claude Max subscription instead of the Gemini free-tier daily cap (REC-024).

**Architecture:** Introduce a `GroundedLLM` provider seam (`scouts/grounded_llm.py`) with Gemini and Claude implementations chosen by a factory reading `AGENT_BACKEND`, and inject it into the `LLMScout` base class. The genai client + grounded-response text extraction move out of `LLMScout` into `GeminiGroundedLLM`; every scout method swaps its `generate_content(...) + _extract_text(...)` for `self._llm.generate(prompt, grounded=...)`. Scout prompts, JSON parsing (`_safe_extract_json`/`_flatten_style_map`), merge and persistence are unchanged. Embeddings stay on Gemini.

**Tech Stack:** Python 3.11, google-genai, claude-agent-sdk (optional extra, lazy-imported), pytest. Tests run in the dev container: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest ...'`. Commit with `SKIP=pytest git commit` (ruff/ruff-format run on commit; if ruff-format reformats, re-`git add -A` and re-commit). Lint authoritative via pre-commit, NOT bare `ruff check`. Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- `src/agentic_librarian/scouts/grounded_llm.py` — **new**: `GroundedLLM` Protocol, module-level `_extract_text`, `GeminiGroundedLLM`, `ClaudeGroundedLLM` (lazy-imports `claude_agent_sdk`), `get_grounded_llm()` factory. (Task 1)
- `src/agentic_librarian/scouts/metadata_scout.py` — **modify**: `LLMScout.__init__` injects a `GroundedLLM`; remove the `genai.Client` construction and the `_extract_text` method from `LLMScout`; rewrite the 7 scout call sites to use `self._llm.generate(...)`. (Task 2)
- `test/unit/test_grounded_llm.py` — **new**: provider + factory unit tests. (Task 1)
- `test/unit/test_style_scout.py`, `test/unit/test_trope_scout.py` — **modify**: migrate from patching `genai.Client` to injecting a fake `GroundedLLM`. (Task 2)
- `test/unit/test_metadata_scout.py` — **modify**: the `_extract_text` test moves to import from `grounded_llm`. (Task 2)
- `.env.example`, `docs/project_notes/decisions.md` (ADR-044), `docs/project_notes/issues.md` (REC-024 resolved) — **modify**. (Task 3)

---

## Task 1: GroundedLLM provider seam

**Files:**
- Create: `src/agentic_librarian/scouts/grounded_llm.py`
- Test: `test/unit/test_grounded_llm.py`

- [ ] **Step 1: Write the failing tests** in new file `test/unit/test_grounded_llm.py`

```python
from unittest.mock import MagicMock

import agentic_librarian.scouts.grounded_llm as gl


def test_extract_text_prefers_text_then_parts():
    direct = MagicMock()
    direct.text = "hello"
    assert gl._extract_text(direct) == "hello"

    grounded = MagicMock()
    grounded.text = None
    part = MagicMock()
    part.text = '{"x": 1}'
    grounded.candidates = [MagicMock(content=MagicMock(parts=[part]))]
    assert gl._extract_text(grounded) == '{"x": 1}'

    empty = MagicMock()
    empty.text = None
    empty.candidates = []
    assert gl._extract_text(empty) is None


def test_gemini_generate_grounded_and_plain(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            captured["model"] = model
            captured["config"] = config
            resp = MagicMock()
            resp.text = "RESULT"
            return resp

    class FakeClient:
        def __init__(self, *a, **k):
            self.models = FakeModels()

    monkeypatch.setattr(gl.genai, "Client", FakeClient)
    monkeypatch.setenv("USE_SEARCH_GROUNDING", "1")
    monkeypatch.setenv("GROUNDING_MODEL", "gemini-test")

    llm = gl.GeminiGroundedLLM(api_key="k")
    assert llm.generate("p", grounded=True) == "RESULT"
    assert captured["config"]["tools"] == [{"google_search": {}}]
    assert captured["model"] == "gemini-test"

    # grounded=False -> no tools
    llm.generate("p", grounded=False)
    assert captured["config"]["tools"] == []


def test_gemini_respects_use_search_grounding_flag(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            captured["config"] = config
            resp = MagicMock()
            resp.text = "x"
            return resp

    class FakeClient:
        def __init__(self, *a, **k):
            self.models = FakeModels()

    monkeypatch.setattr(gl.genai, "Client", FakeClient)
    monkeypatch.setenv("USE_SEARCH_GROUNDING", "0")
    gl.GeminiGroundedLLM(api_key="k").generate("p", grounded=True)
    assert captured["config"]["tools"] == []  # flag off -> no grounding even when grounded=True


def test_factory_selects_backend(monkeypatch):
    monkeypatch.setattr(gl.genai, "Client", lambda *a, **k: MagicMock())
    monkeypatch.setenv("AGENT_BACKEND", "adk")
    assert isinstance(gl.get_grounded_llm("k"), gl.GeminiGroundedLLM)
    monkeypatch.delenv("AGENT_BACKEND", raising=False)
    assert isinstance(gl.get_grounded_llm("k"), gl.GeminiGroundedLLM)
    monkeypatch.setenv("AGENT_BACKEND", "claude")
    assert isinstance(gl.get_grounded_llm("k"), gl.ClaudeGroundedLLM)


def test_claude_generate_collects_result_and_sets_tools(monkeypatch):
    captured = {}

    class FakeMsg:
        def __init__(self, result):
            self.result = result

    async def fake_query(prompt, options):
        captured["allowed_tools"] = options.allowed_tools
        captured["model"] = options.model
        yield FakeMsg(None)
        yield FakeMsg("CLAUDE_JSON")

    fake_sdk = MagicMock()
    fake_sdk.query = fake_query
    fake_sdk.ClaudeAgentOptions = lambda **kw: MagicMock(**kw)
    monkeypatch.setitem(__import__("sys").modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setenv("CLAUDE_MODEL", "claude-test")

    llm = gl.ClaudeGroundedLLM()
    assert llm.generate("p", grounded=True) == "CLAUDE_JSON"
    assert captured["allowed_tools"] == ["WebSearch"]
    assert captured["model"] == "claude-test"
    assert llm.generate("p", grounded=False) == "CLAUDE_JSON"
    assert captured["allowed_tools"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_grounded_llm.py -v'`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentic_librarian.scouts.grounded_llm'`.

- [ ] **Step 3: Create `src/agentic_librarian/scouts/grounded_llm.py`**

```python
"""Backend-selectable grounded-LLM provider for the enrichment scouts. The single AGENT_BACKEND knob
(shared with the recommendation mesh) picks Gemini grounding (default) or Claude WebSearch, so an
`AGENT_BACKEND=claude` run enriches off the Max subscription instead of the Gemini free-tier daily cap
(REC-024). Embeddings stay on Gemini (separate, higher quota)."""

from __future__ import annotations

import asyncio
import os
from typing import Protocol, runtime_checkable

from agentic_librarian.llm_retry import genai_http_options
from google import genai


@runtime_checkable
class GroundedLLM(Protocol):
    def generate(self, prompt: str, grounded: bool = True) -> str:
        """Return the model's text for `prompt`. When `grounded`, perform web-grounded generation."""
        ...


def _extract_text(response) -> str | None:
    """Return response text, falling back to concatenated candidate parts. Grounded/multi-part
    responses can leave ``response.text`` empty even though the answer is in the candidate parts."""
    if getattr(response, "text", None):
        return response.text
    try:
        parts = response.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return None
    texts = [p.text for p in parts if getattr(p, "text", None)]
    return "".join(texts) if texts else None


class GeminiGroundedLLM:
    """Grounded generation via Gemini's google_search tool — the prior scout behavior, relocated."""

    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        self.api_key = api_key or os.environ.get("GOOGLE_SEARCH_API_KEY")
        self.model_name = (
            model_name or os.environ.get("GROUNDING_MODEL") or os.environ.get("EXPLORER_MODEL") or "gemini-2.5-flash"
        )
        self._client = genai.Client(api_key=self.api_key, http_options=genai_http_options())

    def generate(self, prompt: str, grounded: bool = True) -> str:
        use_grounding = grounded and os.environ.get("USE_SEARCH_GROUNDING", "1") == "1"
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"tools": [{"google_search": {}}] if use_grounding else []},
        )
        return _extract_text(response) or ""


class ClaudeGroundedLLM:
    """Grounded generation via the Claude Agent SDK (WebSearch tool), Max-subscription quota."""

    _SYSTEM = (
        "You are a book-metadata extraction assistant. Use web search to verify facts; never invent "
        "details. Return ONLY the raw JSON object the user's instructions specify — no prose, no code fences."
    )

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

    def generate(self, prompt: str, grounded: bool = True) -> str:
        return asyncio.run(self._agenerate(prompt, grounded))

    async def _agenerate(self, prompt: str, grounded: bool) -> str:
        from claude_agent_sdk import ClaudeAgentOptions, query

        options = ClaudeAgentOptions(
            system_prompt=self._SYSTEM,
            model=self.model,
            allowed_tools=["WebSearch"] if grounded else [],
        )
        text = ""
        async for message in query(prompt=prompt, options=options):
            result_val = getattr(message, "result", None)
            if result_val and isinstance(result_val, str):
                text = result_val
        return text


def get_grounded_llm(api_key: str | None = None) -> GroundedLLM:
    """Pick the grounding-LLM provider from AGENT_BACKEND (default/'adk' -> Gemini; 'claude' -> Claude)."""
    choice = os.environ.get("AGENT_BACKEND", "adk").strip().lower()
    if choice == "claude":
        return ClaudeGroundedLLM()
    return GeminiGroundedLLM(api_key)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_grounded_llm.py -v'`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/scouts/grounded_llm.py test/unit/test_grounded_llm.py
SKIP=pytest git commit -m "feat: GroundedLLM seam (Gemini/Claude) selected by AGENT_BACKEND (REC-024)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Route LLMScout through the seam

**Files:**
- Modify: `src/agentic_librarian/scouts/metadata_scout.py` (`LLMScout` + the 4 scout classes)
- Modify: `test/unit/test_style_scout.py`, `test/unit/test_trope_scout.py`, `test/unit/test_metadata_scout.py`

### 2a. LLMScout base: inject the provider, drop the client + `_extract_text`

- [ ] **Step 1: Add the import.** At the top of `metadata_scout.py`, add to the first-party imports:

```python
from agentic_librarian.scouts.grounded_llm import GroundedLLM, get_grounded_llm
```

- [ ] **Step 2: Rewrite `LLMScout.__init__`.** Replace the current body:

```python
    def __init__(self, api_key: str = None, model_name: str = None, llm: GroundedLLM | None = None):
        # Fallback to GOOGLE_SEARCH_API_KEY if no specific key provided (also used by AudiobookScout's
        # Custom Search and by the Gemini provider/embeddings).
        key = api_key or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not key:
            raise ValueError(f"{self.__class__.__name__} requires a Google API key.")
        super().__init__(key)
        # Backend-selectable grounding LLM (Gemini default; Claude when AGENT_BACKEND=claude). Injectable
        # for tests. model_name kept for back-compat; the Gemini provider owns the model (GROUNDING_MODEL).
        self.model_name = (
            model_name or os.environ.get("GROUNDING_MODEL") or os.environ.get("EXPLORER_MODEL") or "gemini-2.5-flash"
        )
        self._llm = llm or get_grounded_llm(self.api_key)
```

This removes the `self._client = genai.Client(...)` line.

- [ ] **Step 3: Delete the `_extract_text` method from `LLMScout`** (it now lives in `grounded_llm.py`). Keep `_safe_extract_json` exactly as-is.

### 2b. Rewrite the scout call sites (7 of them)

- [ ] **Step 4: StyleScout — `scout_work_style`.** Replace the `use_grounding = ...` + `generate_content(...)` block and the final return so the method ends with:

```python
        text = self._llm.generate(prompt, grounded=True)
        return _flatten_style_map(self._safe_extract_json(text, "Work Style", title))
```

(Delete the `use_grounding = os.environ.get(...)` line and the `response = self._client.models.generate_content(...)` call.)

- [ ] **Step 5: StyleScout — `scout_author_style`.** Same change; method ends with:

```python
        text = self._llm.generate(prompt, grounded=True)
        return _flatten_style_map(self._safe_extract_json(text, "Author Style", name))
```

- [ ] **Step 6: StyleScout — `scout_narrator_style`.** Same change; method ends with:

```python
        text = self._llm.generate(prompt, grounded=True)
        return _flatten_style_map(self._safe_extract_json(text, "Narrator Style", name))
```

- [ ] **Step 7: AudiobookScout — `extract_metadata_with_gemini`.** This is **plain** extraction (no grounding). Replace the two `generate_content` calls:

```python
        text = self._llm.generate(prompt, grounded=False)
        data = self._safe_extract_json(text, title, author)
        if data is None:
            text = self._llm.generate(prompt + "\nJSON ONLY.", grounded=False)
            data = self._safe_extract_json(text, title, author, retry_count=1)
```

(Keep the key-normalization block below it unchanged.)

- [ ] **Step 8: DirectKnowledgeScout — `scout_audiobook`.** Replace the `use_grounding` + two `generate_content` calls:

```python
        text = self._llm.generate(prompt, grounded=True)
        data = self._safe_extract_json(text, title, author)
        if data is None:
            prompt += "\n\nSTRICT: Return valid JSON ONLY."
            text = self._llm.generate(prompt, grounded=True)
            data = self._safe_extract_json(text, title, author, retry_count=1)
```

(Keep whatever return/normalization follows.)

- [ ] **Step 9: LLMTropeScout — `search`.** Replace the `use_grounding` + `generate_content` so the method ends with:

```python
        text = self._llm.generate(prompt, grounded=True)
        return self._safe_extract_json(text, "Tropes", title) or {"tropes": []}
```

- [ ] **Step 10: Remove the now-unused genai import.** The scouts no longer construct a client, so `from google import genai` in `metadata_scout.py` is unused (ruff will flag F401 and fail the commit). Delete that import line. (Verify `genai` isn't referenced elsewhere in the file first — it should only have been used by the deleted `LLMScout` client.)

- [ ] **Step 11: Sanity-check no stragglers.** Run:

`docker exec agentic_librarian_app sh -lc 'cd /app && grep -nE "_client\.models\.generate_content|self\._extract_text|self\._client|google import genai" src/agentic_librarian/scouts/metadata_scout.py'`
Expected: **no output** (all generate_content / _client / _extract_text uses and the genai import are gone from the scouts file).

### 2c. Migrate the scout unit tests to inject a fake provider

- [ ] **Step 12: Rewrite `test/unit/test_style_scout.py`** to inject a fake `GroundedLLM` instead of patching `genai.Client`:

```python
from unittest.mock import patch

from agentic_librarian.scouts.metadata_scout import StyleScout


class _FakeLLM:
    """A GroundedLLM stub returning a fixed JSON string for every call."""

    def __init__(self, text: str):
        self._text = text

    def generate(self, prompt: str, grounded: bool = True) -> str:
        return self._text


def test_scout_author_style():
    scout = StyleScout(api_key="fake-key", llm=_FakeLLM('{"pacing": "fast", "tone": "cynical", "style": "minimalist"}'))
    style = scout.scout_author_style("Ernest Hemingway")
    assert style["pacing"] == "fast"
    assert style["tone"] == "cynical"
    assert style["style"] == "minimalist"


def test_scout_narrator_style():
    scout = StyleScout(
        api_key="fake-key",
        llm=_FakeLLM('{"pacing": "steady", "voice_differentiation": "excellent", "emotional_range": "wide"}'),
    )
    style = scout.scout_narrator_style("Jefferson Mays")
    assert style["pacing"] == "steady"
    assert style["voice_differentiation"] == "excellent"
    assert style["emotional_range"] == "wide"


def test_style_scout_search_mode():
    scout = StyleScout(api_key="fake-key", llm=_FakeLLM('{"pacing": "fast"}'))
    res = scout.search("The Expanse", "James S.A. Corey", narrators=["Jefferson Mays"])
    assert "author_style" in res
    assert "narrator_styles" in res
    assert "Jefferson Mays" in res["narrator_styles"]
    assert res["author_style"]["pacing"] == "fast"


def test_work_style_baseline_falls_back_to_scouted_author_style():
    scout = StyleScout(api_key="fake-key", llm=_FakeLLM("{}"))
    with (
        patch.object(scout, "scout_author_style", return_value={"pacing": "fast"}),
        patch.object(scout, "scout_work_style", return_value={}) as m_work,
        patch.object(scout, "scout_narrator_style", return_value={}),
    ):
        scout.search("Book", "Author")
    assert m_work.call_args.kwargs["author_baseline"] == {"pacing": "fast"}


def test_work_style_baseline_prefers_db_baseline_when_provided():
    scout = StyleScout(api_key="fake-key", llm=_FakeLLM("{}"))
    with (
        patch.object(scout, "scout_author_style", return_value={"pacing": "fast"}),
        patch.object(scout, "scout_work_style", return_value={}) as m_work,
    ):
        scout.search("Book", "Author", author_styles={"tone": "dark"})
    assert m_work.call_args.kwargs["author_baseline"] == {"tone": "dark"}
```

- [ ] **Step 13: Rewrite `test/unit/test_trope_scout.py`** the same way:

```python
from agentic_librarian.scouts.metadata_scout import LLMTropeScout


class _FakeLLM:
    def __init__(self, text: str):
        self._text = text

    def generate(self, prompt: str, grounded: bool = True) -> str:
        return self._text


def test_llm_trope_scout():
    payload = """
    {
        "tropes": [
            {
                "trope_name": "Found Family",
                "description": "A group of people who are not related by blood but form a deep familial bond.",
                "relevance_score": 0.9,
                "justification": "The crew of the Rocinante forms a tight-knit family unit throughout the series."
            }
        ]
    }
    """
    scout = LLMTropeScout(api_key="fake-key", llm=_FakeLLM(payload))
    res = scout.search("Leviathan Wakes", "James S.A. Corey")
    assert "tropes" in res
    assert len(res["tropes"]) == 1
    assert res["tropes"][0]["trope_name"] == "Found Family"
    assert res["tropes"][0]["relevance_score"] == 0.9
```

- [ ] **Step 14: Update the `_extract_text` test in `test/unit/test_metadata_scout.py`.** That test currently calls `scout._extract_text(...)`, which no longer exists on the scout. Replace `test_extract_text_falls_back_to_candidate_parts` with a version that exercises the relocated function:

```python
def test_extract_text_falls_back_to_candidate_parts():
    """When response.text is empty (grounded responses), text comes from the candidate parts."""
    from unittest.mock import MagicMock

    from agentic_librarian.scouts.grounded_llm import _extract_text

    direct = MagicMock()
    direct.text = "hello"
    assert _extract_text(direct) == "hello"

    grounded = MagicMock()
    grounded.text = None
    part = MagicMock()
    part.text = '{"x": 1}'
    grounded.candidates = [MagicMock(content=MagicMock(parts=[part]))]
    assert _extract_text(grounded) == '{"x": 1}'
```

(Leave `test_safe_extract_json_handles_fences_prose_and_none` as-is — `_safe_extract_json` still lives on `LLMScout`, and constructing `LLMTropeScout(api_key="fake-key")` is offline-safe.)

- [ ] **Step 15: Run the scout tests**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_style_scout.py test/unit/test_trope_scout.py test/unit/test_metadata_scout.py -o addopts="" -m "not api_dependent" -v 2>&1 | tail -25'`
Expected: all PASS (the migrated scout tests + the relocated `_extract_text` test).

- [ ] **Step 16: Run the full offline suite** to confirm nothing else broke (the `LLMScout` change affects every scout):

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest -q -p no:cacheprovider -m "not api_dependent and not db_integration"'`
Expected: all pass.

- [ ] **Step 17: Commit**

```bash
git add src/agentic_librarian/scouts/metadata_scout.py test/unit/test_style_scout.py test/unit/test_trope_scout.py test/unit/test_metadata_scout.py
SKIP=pytest git commit -m "refactor: route LLMScout through the GroundedLLM seam (REC-024)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: ETL audit, config, ADR, and a live Claude-scout check

**Files:**
- Modify: `.env.example`, `docs/project_notes/decisions.md`, `docs/project_notes/issues.md`
- Test: `test/unit/test_metadata_scout.py` (one `api_dependent` live test)

- [ ] **Step 1: Audit for genai usage outside the seam on the scout/ETL path.** Run:

`docker exec agentic_librarian_app sh -lc 'cd /app && grep -rnE "genai\.Client|generate_content" src/agentic_librarian/scouts/ src/agentic_librarian/orchestration/'`
Expected: the only `genai.Client` matches are in `grounded_llm.py` (the seam), `trope_manager.py`, and `style_manager.py` (embeddings — intentionally Gemini). No `generate_content` in `orchestration/`. If any scout/ETL generation path still constructs a client directly, route it through `get_grounded_llm()` the same way. Record the audit result in the commit message.

- [ ] **Step 2: Update `.env.example`.** Find the `AGENT_BACKEND` line (added in the pluggable-backend work) and expand its comment; if absent, add it near the model config:

```
# Selects the LLM backend for BOTH the recommendation mesh AND the enrichment scouts (and therefore
# the Flow-1 Dagster ETL, which uses the same scouts): 'adk' (default) = Gemini; 'claude' = the Claude
# Agent SDK on your Max subscription (needs the in-container `claude` CLI authenticated). Embeddings
# always stay on Gemini (gemini-embedding-001, separate higher quota). Set AGENT_BACKEND=claude to run
# a full reading-history ETL off Claude instead of the Gemini free-tier daily cap.
AGENT_BACKEND=adk
```

- [ ] **Step 3: Append ADR-044** to the end of `docs/project_notes/decisions.md`:

```markdown

### ADR-044: GroundedLLM Seam — Enrichment Scouts Follow AGENT_BACKEND (2026-06-02)
**Context:**
- ADR-041 made the recommendation mesh backend-selectable, but the enrichment LLM scouts stayed on
  Gemini. An `AGENT_BACKEND=claude` run (and the Flow-1 ETL) still hit the Gemini free-tier
  `generate_content` daily cap (20/day), stretching a full reading-history ingest into ~weeks (REC-024).

**Decision:**
- Introduce a `GroundedLLM` provider seam (`scouts/grounded_llm.py`: `generate(prompt, grounded=)`) with
  `GeminiGroundedLLM` (google_search) and `ClaudeGroundedLLM` (Agent SDK WebSearch, run synchronously via
  `asyncio.run`), chosen by `get_grounded_llm()` reading the SAME `AGENT_BACKEND` knob. `LLMScout` takes
  the provider (injectable); all four scouts call `self._llm.generate(...)`. Prompts, JSON parsing, merge
  and persistence are unchanged. Embeddings stay on Gemini (separate, higher quota — not the bottleneck).

**Consequences:**
- One knob flips the whole pipeline (recommendation + batch ETL) between Gemini and Claude; default is
  byte-for-byte Gemini. Claude extraction quality vs Gemini grounding is validated by a live check.
  `ClaudeGroundedLLM.generate` must be called from a synchronous context (it uses `asyncio.run`); scouts
  always are. A full ETL on Claude issues many WebSearch calls — validate Agent SDK rate limits on a
  small batch first.
```

- [ ] **Step 4: Resolve REC-024** in `docs/project_notes/issues.md` — set its `- **Status**:` line to:

```
- **Status**: Resolved (2026-06-02) — GroundedLLM seam (scouts/grounded_llm.py): GeminiGroundedLLM + ClaudeGroundedLLM chosen by AGENT_BACKEND, injected into LLMScout. All four LLM scouts route through it; AGENT_BACKEND=claude runs the scouts (recommendation + Flow-1 ETL) on the Max subscription. Embeddings stay Gemini. ADR-044.
```

- [ ] **Step 5: Add an `api_dependent` live Claude-scout test** to `test/unit/test_metadata_scout.py`:

```python
@pytest.mark.api_dependent
def test_claude_grounded_scouts_produce_styles_and_tropes(monkeypatch):
    """Live (needs an authenticated claude CLI): AGENT_BACKEND=claude yields usable style/trope JSON."""
    monkeypatch.setenv("AGENT_BACKEND", "claude")
    from agentic_librarian.scouts.metadata_scout import LLMTropeScout, StyleScout

    tropes = LLMTropeScout(api_key="x").search("The Way of Kings", "Brandon Sanderson")
    assert tropes.get("tropes"), "expected non-empty tropes from the Claude LLMTropeScout"
    style = StyleScout(api_key="x").scout_author_style("Brandon Sanderson")
    assert style, "expected non-empty author style from the Claude StyleScout"
```

- [ ] **Step 6: (Manual) run the live test** once the `claude` CLI is authenticated and you want to spend Claude quota:

Run: `docker exec -e AGENT_BACKEND=claude agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_metadata_scout.py::test_claude_grounded_scouts_produce_styles_and_tropes -o addopts="" -m api_dependent -v -s'`
Expected: PASS (non-empty tropes + style). If the output is empty or malformed, do NOT hack the test — report it; the scout prompts may need light tuning for Claude (a separate follow-up, noted in the spec's Risks).

- [ ] **Step 7: Commit**

```bash
git add .env.example docs/project_notes/decisions.md docs/project_notes/issues.md test/unit/test_metadata_scout.py
SKIP=pytest git commit -m "docs+test: ADR-044, AGENT_BACKEND env docs, resolve REC-024, live Claude-scout test

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] **Full offline suite:** `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest -q -p no:cacheprovider -m "not api_dependent and not db_integration"'` — all pass (prior count + the new grounded_llm tests; the migrated scout tests still pass).
- [ ] **pre-commit clean:** `docker exec agentic_librarian_app sh -lc 'cd /app && pre-commit run --all-files'` (or rely on the per-commit hooks) — ruff + ruff-format pass.
- [ ] **Default path unchanged:** confirm that with `AGENT_BACKEND` unset, the scouts still construct a `GeminiGroundedLLM` (the `test_factory_selects_backend` test covers this).
- [ ] Use **superpowers:finishing-a-development-branch** to open the PR. (Live Claude-scout verification — Task 3 Step 6 — and a Claude-backed ETL smoke can follow on quota, like prior specs.)
