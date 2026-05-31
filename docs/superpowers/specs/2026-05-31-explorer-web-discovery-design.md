# Spec 2 — Explorer External Web Discovery

**Date:** 2026-05-31
**Status:** Approved design (pre-implementation)
**Part of:** ADR-035 phased mesh delivery — spec 2 of 4.

## Goal

Give the Explorer agent real **grounded web discovery** so the mesh can find new/unread
books — including recent releases outside the model's training data — and verify that the
**Librarian → Explorer** delegation surfaces those results end-to-end.

**Success:** a discovery request to the Librarian is delegated to the Explorer, which
performs a grounded web search and returns real candidate books. (The chain runs and
returns book-naming content; grounding / anti-hallucination is demonstrated by the spike
and a manual check, not a brittle automated ground-truth assertion.)

## Approach

**Approach A — `GoogleSearchTool(bypass_multi_tools_limit=True)` (spiked & verified in ADK 2.1.0).**
Give the `ExplorerAgent` ADK's search tool with `bypass_multi_tools_limit=True`. In 2.1.0
this works on its own — no Interactions API (`use_interactions_api` isn't a real param in
this version) — grounds correctly, and, crucially, works in the **sub-agent** scenario
(built-in tools are otherwise forbidden in sub-agents; the bypass converts it to a
function-calling tool).

Spike evidence (live, ADK 2.1.0, our Developer-API key): both a standalone Explorer and a
root Librarian → Explorer (`AgentTool`) returned real **2024** titles — *Empire of the
Damned* (Kristoff), *The Daughters' War* (Buehlman), *The Book that Broke the World*
(Lawrence), *In the Shadow of Their Dying* (Fletcher).

**Rejected:** (B) a custom grounded `FunctionTool` — a viable fallback, but A is ADK-native,
near-zero custom code, and exposes grounding metadata for free. (C) restructuring so the
Explorer is the root agent — breaks the orchestrator design (ADR-019).

## Components

### `agents/services.py` — `ExplorerAgent`
- `tools=[GoogleSearchTool(bypass_multi_tools_limit=True)]` (import from
  `google.adk.tools.google_search_tool`).
- **Model:** `os.environ.get("EXPLORER_MODEL", "gemini-2.5-flash")` — its own model
  (discovery benefits from the stronger model, and it's the verified-working one with the
  grounded search). The other agents keep `GEMINI_MODEL` (default `gemini-2.5-flash-lite`).
- **Instruction (tightened):** use `google_search` to find **real** books matching the
  request; bias toward recent / lesser-known titles unlikely to already be in a standard
  personal library; return a handful as `Title — Author — one-line why it fits`; **only
  report what search returns — never invent titles** (anti-hallucination).

### `.env.example`
- Document `EXPLORER_MODEL` (default `gemini-2.5-flash`).

### Not touched
- `search_strategies.py` stays as-is (orphaned A/B benchmark); cleanup deferred to a later pass.

## Data flow

```
conv.send("find me something new like The Blade Itself, but published recently")
  → Librarian delegates to Explorer (AgentTool)
  → Explorer LLM calls google_search (grounded web search)
  → real recent books
  → Explorer formats: Title — Author — one-line fit
  → surfaces back through the Librarian
```

## Error handling
- The instruction forbids hallucinated fallbacks: if search yields nothing, the Explorer
  reports that rather than inventing titles.
- The benign `[EXPERIMENTAL] JSON_SCHEMA_FOR_FUNC_DECL` UserWarning from ADK is accepted
  (internal; functionality works).

## Testing
- **Mock unit** (CI, no API): `ExplorerAgent` is configured with a `GoogleSearchTool` in
  its `tools`, and the Explorer's model resolves from `EXPLORER_MODEL`. (Grounding can't be
  meaningfully mocked, so we assert configuration.)
- **`@pytest.mark.api_dependent` live:** run Librarian → Explorer for a recent query
  ("a grimdark fantasy novel published in 2024") and assert a non-empty, book-naming
  response. Strict grounding-correctness stays a manual check — the spike already showed
  real 2024 titles, and a ground-truth automated assertion would be brittle as results vary.

## Out of scope (later specs)
- Critic ranking / dedup of the Explorer's candidates; persisting discoveries to the DB
  (Spec 3/4).
- Surfacing source citations (the grounding metadata is available but not yet shown).
- Full multi-agent recommendation quality (Spec 4).
- Removing / repurposing `search_strategies.py`.
