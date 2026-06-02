# Claude-Native Enrichment Scouts (REC-024) — Design

**Date:** 2026-06-02
**Branch:** `spec/claude-enrichment-scouts` (off `main` after PR #25 merged)
**Status:** Approved design — ready for implementation plan

## Context

PR #23 (ADR-041) made the recommendation **mesh** (Analyst/Explorer/Critic) backend-selectable via
`AGENT_BACKEND`, but the **enrichment scouts** stayed hardwired to Gemini. So a `AGENT_BACKEND=claude`
run still calls Gemini `generate_content` during `enrich_and_persist_work` — the LLM scouts
(StyleScout, LLMTropeScout, DirectKnowledgeScout, AudiobookScout) do Gemini-native grounding, and that
hits the free-tier daily cap (20 `generate_content`/day on `gemini-2.5-flash`). The same scouts power
the Flow-1 Dagster **ETL** (batch CSV → reading history + enrichment), where the daily cap stretches a
full reading-history ingest into ~weeks. REC-023 made `ScoutManager.enrich` tolerate a scout failing,
but the enrichment still produces nothing useful for styles/tropes under the cap.

This spec makes the enrichment LLM calls **backend-selectable** under the *same* `AGENT_BACKEND` knob,
so setting `AGENT_BACKEND=claude` runs the scouts (and therefore both the recommendation pipeline and
the batch ETL) on the Claude Max subscription instead of the Gemini free tier. Embeddings stay on
Gemini (`gemini-embedding-001` is a separate, much higher quota — never the bottleneck).

## Goals / Non-goals

**Goals**
1. One knob (`AGENT_BACKEND`) selects the grounding-LLM backend for **all** LLM scouts.
2. `AGENT_BACKEND=claude` makes the recommendation pipeline AND the Flow-1 ETL enrich off Claude.
3. No duplication of scout prompt/parse logic across backends (one seam, like `prompts.py` for the mesh).
4. Default (`AGENT_BACKEND` unset / `adk`) preserves current Gemini behavior exactly.

**Non-goals (YAGNI / deferred)**
- Moving embeddings off Gemini (separate high quota; stays Gemini).
- A separate per-scout or per-flow backend knob (single `AGENT_BACKEND` only).
- Changing scout prompts, the merge logic, or the persistence contract.
- Audiobook-specific quality work (AudiobookScout is included mechanically but its scrape pathway is unchanged).

## Design

### Component 1 — `GroundedLLM` provider seam (`scouts/grounded_llm.py`, new)

A small Protocol the scouts call instead of touching a genai client directly:

```python
class GroundedLLM(Protocol):
    def generate(self, prompt: str, grounded: bool = True) -> str: ...
```

- `grounded=True` → the provider performs web-grounded generation (Gemini `google_search` / Claude
  `WebSearch`). `grounded=False` → plain generation, no tools (AudiobookScout extracts from already-
  scraped Audible text).
- Returns the model's **text** (the provider does any response-part extraction internally).

Two implementations:

- **`GeminiGroundedLLM`** — owns a `genai.Client(http_options=genai_http_options())` (the REC-020
  retry), calls `client.models.generate_content(model=GROUNDING_MODEL, contents=prompt, config={"tools":
  [{"google_search": {}}]} if grounded and USE_SEARCH_GROUNDING else {"tools": []})`, and returns text
  via the existing `_extract_text` logic (moved here from `LLMScout`). This is the current behavior,
  byte-for-byte, just relocated.
- **`ClaudeGroundedLLM`** — a **synchronous** wrapper over the Claude Agent SDK: runs `query(prompt=...,
  options=ClaudeAgentOptions(system_prompt=<generic extractor instruction>, model=CLAUDE_MODEL,
  allowed_tools=["WebSearch"] if grounded else []))` via `asyncio.run`, collects the `ResultMessage`
  text, and returns it. Output is JSON-as-text (the scout prompts already say "Return ONLY a raw JSON
  object"), parsed downstream by the unchanged `_safe_extract_json`.

Factory (mirrors `agents/backends.get_backend`):

```python
def get_grounded_llm(api_key: str | None = None) -> GroundedLLM:
    choice = os.environ.get("AGENT_BACKEND", "adk").strip().lower()
    if choice == "claude":
        return ClaudeGroundedLLM()
    return GeminiGroundedLLM(api_key)  # 'adk'/default/anything-else -> Gemini (back-compat)
```

(`adk` maps to Gemini here — `AGENT_BACKEND` already uses `adk` to mean "the Gemini path".)

### Component 2 — `LLMScout` refactor (`scouts/metadata_scout.py`)

- `LLMScout.__init__(api_key=None, model_name=None, llm: GroundedLLM | None = None)`: resolve
  `self._llm = llm or get_grounded_llm(self.api_key)`. The `genai.Client` construction moves out of
  `LLMScout` into `GeminiGroundedLLM`; `_extract_text` moves to `GeminiGroundedLLM`. `model_name` stays
  for back-compat but the model is owned by the provider (the Gemini provider reads `GROUNDING_MODEL`).
- Each scout method changes from
  `response = self._client.models.generate_content(...); data = self._safe_extract_json(self._extract_text(response), ...)`
  to
  `text = self._llm.generate(prompt, grounded=use_grounding); data = self._safe_extract_json(text, ...)`.
  Applies uniformly to `StyleScout` (3 methods), `LLMTropeScout`, `DirectKnowledgeScout`, and
  `AudiobookScout` (`grounded=False` for its extraction). `_safe_extract_json` / `_flatten_style_map`
  are unchanged.

### Component 3 — ETL respects the knob (no code change expected)

The Dagster Flow-1 enrichment goes through `create_scout_manager()` → the same `LLMScout` subclasses,
which now resolve their backend from `AGENT_BACKEND`. So `AGENT_BACKEND=claude dagster ...` runs the
batch on Claude with no orchestration changes. Implementation step: **audit** `orchestration/` and
`scouts/` for any direct `genai.Client`/`generate_content` use outside the seam and confirm none remain
on the scout path (embeddings via `TropeManager`/`StyleManager` are intentionally excluded).

### Component 4 — Config & docs

- `.env.example`: document that `AGENT_BACKEND` now governs the mesh **and** the enrichment scouts
  **and** the Flow-1 ETL (claude → Max quota; default/adk → Gemini). Note embeddings stay Gemini.
- New ADR-044 recording the GroundedLLM seam + single-knob decision.

## Testing

Offline (CI):
- `GeminiGroundedLLM.generate` returns text for a mocked `genai.Client` (grounded and plain configs).
- `ClaudeGroundedLLM.generate` returns the result text for a mocked `query()` async generator; assert
  `WebSearch` is in `allowed_tools` when `grounded=True` and absent when `grounded=False`.
- `get_grounded_llm()` returns `ClaudeGroundedLLM` for `AGENT_BACKEND=claude`, `GeminiGroundedLLM`
  otherwise.
- `LLMScout` subclasses parse correctly with an **injected fake** `GroundedLLM` returning canned JSON
  (e.g. StyleScout → `_flatten_style_map` output; LLMTropeScout → tropes list). Existing scout tests
  migrate from patching `genai.Client` to injecting a fake provider.

Live (`api_dependent`, manual):
- With `AGENT_BACKEND=claude`, a StyleScout + LLMTropeScout call for a known book returns non-empty,
  well-formed style/trope JSON (validates Claude `WebSearch` grounding ≈ `google_search` for extraction).

## Success criteria

- `AGENT_BACKEND=claude` runs the scouts on Claude (recommendation + ETL); default stays Gemini, with
  current behavior byte-for-byte.
- All four LLM scouts route through the seam; no per-scout duplication; parse/merge/persist unchanged.
- Embeddings remain Gemini.
- Offline suite green; one live Claude-scout check confirms usable extraction.

## Risks

- **Extraction quality on Claude `WebSearch`** is unverified vs Gemini `google_search` — the live check
  gates it; if weak, the prompts may need light tuning (separate follow-up, not this spec).
- **Async-in-sync:** `ClaudeGroundedLLM.generate` uses `asyncio.run`; safe only when called from a
  thread without a running loop. Scouts are always invoked synchronously (`ScoutManager.enrich` is sync;
  the pipeline wraps it in `asyncio.to_thread`). Documented as a constraint; a guard/`anyio` fallback can
  be added if a running-loop caller ever appears.
- **Throughput/cost:** a Claude run issues several `WebSearch` calls per book; a full ETL batch makes
  many. Validate the Agent SDK's own rate limits on a small batch before a full reading-history ingest.

## Issue tracking

Resolves REC-024. Builds on ADR-041 (RecommendationBackend seam) and REC-023 (per-scout isolation).
New ADR-044 for the GroundedLLM seam.
