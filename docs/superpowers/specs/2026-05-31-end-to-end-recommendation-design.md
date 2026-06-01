# Spec 4: End-to-End Recommendation + Trope-RAG — Design

**Status:** Approved (2026-05-31)
**Part of:** ADR-035 phased mesh delivery, spec 4 of 4 (the MVP finish line)
**Predecessors:** Spec 1 (mesh runtime), Spec 2 (Explorer web discovery), Spec 3 (internal retrieval) — all merged.

## Goal

Deliver a reliable end-to-end recommendation: a single `run_recommendation(prompt)` call runs
the full Analyst → (internal candidates) → Explorer → enrichment → Critic chain and returns a
**Trope-RAG-justified, logged** recommendation. Web discoveries become first-class by being
de-duped, enriched, and persisted so the Critic can rank and justify them with the same
DB-backed evidence as internal candidates.

**Scope (decided):** threads **A** (reliable e2e) + **B** (full discover→enrich→persist→rank).
Security hardening (SEC-001/002) is deferred to Spec 5+, but the design keeps the discovery and
write paths structured so it drops in cleanly. Tests run against a deterministic fixture seed;
real-data seeding of the 304-book history is a parallel operational task (ideally a paid Gemini
tier), not a blocker.

## Background: why the current mesh is unreliable

The four agents are wired (Librarian orchestrates Analyst/Explorer/Critic via `AgentTool`), but
the one-shot path has the REC-016 gaps:
1. The conversational Librarian sometimes asks a clarifying question instead of committing to a
   recommendation (non-deterministic one-shot).
2. Delegation runs sometimes end on a tool/transfer event with no text, so `asend` returns
   "(no response)".
3. A web discovery (title/author, **no DB UUID**) gets `{}` from `get_work_details`, so the
   Critic literally cannot rank or justify a fresh discovery.

Root cause of 1 & 2: relying on the **Librarian LLM to orchestrate** the whole flow. Root cause
of 3: discoveries are never persisted, so the Critic's DB-backed tools can't see them.

## Architecture: a `SequentialAgent` recommendation pipeline

`run_recommendation` is re-implemented as a fixed-order ADK **`SequentialAgent`** (the
*RecommendationPipeline*). Each step reads/writes `ctx.session.state`; the sequence is **code**,
not an LLM decision — eliminating the non-determinism (REC-016 #1/#2) by construction. ADK
mechanics verified against the installed 2.1.0 (Context7 `/google/adk-python`): `SequentialAgent`
runs `sub_agents` in order; `LlmAgent(output_key=...)` writes its result to
`ctx.session.state`; custom agents subclass `BaseAgent` and implement
`async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]`, reading/writing
`ctx.session.state` and `yield`ing events.

The conversational multi-turn Librarian (ADR-036) is **untouched** and remains for interactive
chat. A new factory `create_recommendation_pipeline()` builds the `SequentialAgent`;
`create_agent_mesh()` (the Librarian) stays as-is. One-shot recommendations use the pipeline;
interactive chat uses the Librarian.

### Pipeline steps

| # | Step | Type | Reads → Writes (state) |
|---|------|------|------------------------|
| 1 | **Analyst** | `LlmAgent` (+ `get_user_trope_preferences`, `output_schema=Targets`), `output_key="targets"` | user prompt → `targets` (structured: tropes, styles, session_constraints) |
| 2 | **InternalCandidates** | custom `BaseAgent` | `targets` → `candidate_ids` (from `search_internal_database` + `get_unacted_suggestions`) |
| 3 | **Explorer** | `LlmAgent` (+ `google_search`, `output_schema=Discoveries`), `output_key="discoveries"` | prompt + `targets` → `discoveries` (structured list of `{title, author, why}`) |
| 4 | **Enrichment** | custom `BaseAgent` | `discoveries` → appends enriched/de-duped work-ids to `candidate_ids` |
| 5 | **Critic** | `LlmAgent` (+ DB tools), `output_key="recommendation"` | `candidate_ids` + `targets` → `recommendation` (Trope-RAG justified) |
| 6 | **Logger** | custom `BaseAgent` | `recommendation` → calls `log_suggestion`; passes the text through |

**Structured outputs via `output_schema` + tools:** the Analyst and Explorer define Pydantic
`output_schema`s (`Targets`, `Discoveries`) so their results land in `ctx.session.state` as
**validated structured objects**, not prose — removing the brittle parsing that caused REC-016 #2.
ADK 2.1.0 supports `output_schema` *together with* tools on the same `LlmAgent` (verified
empirically: `LlmAgent(tools=[...], output_schema=...)` constructs cleanly, and the LlmAgent
source states it "supports using output_schema and tools together … enforcing structure only on
the reply"; capability present since 1.26.0 — see the ADR-037 update). So the Analyst keeps
`get_user_trope_preferences` and the Explorer keeps `google_search` while still emitting schemas.
Downstream `LlmAgent`s read upstream results via instruction templating (`{targets}`); the custom
agents read the structured objects from state directly. The Critic's final `recommendation` is
human-readable text (no schema). If a live run shows the schema+tools path mishandles grounded
output, `scouts/metadata_scout.py::_safe_extract_json` is the documented fallback (extract it to a
shared helper only if actually needed — YAGNI otherwise).

**Final-text extraction:** `run_recommendation` returns `ctx.session.state["recommendation"]`
directly after the pipeline completes, **not** the last event's text — this is the structural
fix for "(no response)".

## Thread B: discover → de-dup → enrich → persist

The **Enrichment** custom agent (step 4) is the core of B. For each discovery `{title, author}`
in `state["discoveries"]`:

1. **De-dup (Case 1):** look up an existing `Work` by normalized title + author. If found, append
   that work-id to `candidate_ids` and skip enrichment (prefer the existing DB entry).
2. **Enrich + persist (Case 2):** if new, run the existing `ScoutManager.enrich(title, author,
   format)` (the same Flow-1 enrichment the ETL uses), then persist a `Work` + contributors +
   tropes/styles + embeddings. A persisted discovery is a catalog `Work` with **no
   `ReadingHistory`** ("known but unread"); if later read, history is added normally.
3. **Failure isolation:** if a discovery fails to enrich (scout returns nothing, API/network
   error, **429 quota**), it is skipped — the pipeline proceeds with the remaining candidates.

This is exposed as a coarse MCP tool **`enrich_and_persist_work(title, author) -> work_id | None`**
in `mcp/server.py`, so it is reusable and so the **SEC-002 write-authorization boundary can wrap
it later** without restructuring. The Enrichment agent calls this tool.

### DRY: shared row-persist function

`orchestration/assets.py::vectorized_tropes` already contains the logic to turn an enriched-row
dict into persisted Work/Contributor/Style/Trope/Edition/ReadingHistory rows. That logic is
**extracted into a shared module-level function** (e.g. `etl/persist.py::persist_enriched_work`)
that both `vectorized_tropes` (ETL) and `enrich_and_persist_work` (the new tool) call — one
implementation, no duplication. The extraction must preserve the asset's existing behavior
(verified by the existing ETL tests).

## Data flow & state keys

```
state["targets"]        # {tropes: [...], styles: [...], session_constraints: [...]}  (Analyst)
state["candidate_ids"]  # [work_id, ...]   (InternalCandidates, extended by Enrichment)
state["discoveries"]    # [{title, author, why}, ...]   (Explorer)
state["recommendation"] # final justified text   (Critic)
```

The Runner hosts the `SequentialAgent`; `run_recommendation` creates a session, runs the
pipeline to completion, and returns `state["recommendation"]`.

## Trope-RAG & logging

The Critic's Trope-RAG justification already exists in its instruction (anchor on the top trope's
`name`/`description` + the DB `justification` evidence from `get_work_details`). Because enriched
discoveries are now **persisted Works with real tropes**, discoveries receive the same DB-backed
justification as internal candidates. The Logger step calls `log_suggestion(work_id, context,
justification)` for the final pick(s).

## Error handling

- Per-discovery enrich failure (incl. 429) → skip that discovery, continue.
- No candidates at all → the Critic returns a graceful "no strong match" message; still
  logged-safe (nothing to `log_suggestion`, no crash).
- Enrichment is idempotent via de-dup, so re-runs never duplicate Works.
- All DB writes already degrade gracefully (try/except in the MCP write tools; the
  `get_work_details` UUID guard from Spec 2 remains).

## Security-ready structure (SEC-001/002 deferred to Spec 5+)

- **SEC-001 (prompt injection via web grounding):** the Explorer's output is consumed as
  **structured data** (title/author parsed for de-dup/enrich), never fed back as raw instructions
  to downstream agents — a partial trust boundary already in place. Full delimiting/validation of
  web text is Spec 5.
- **SEC-002 (write authorization):** all writes funnel through MCP tools
  (`enrich_and_persist_work`, `log_suggestion`, `update_reading_status`,
  `update_suggestion_status`) — a single choke point an authorization layer wraps later without
  restructuring.

## Testing

- **Deterministic (CI-gated, `db_integration`):**
  - `persist_enriched_work` (the extracted shared function): given a fake enriched-row dict, it
    creates the expected Work/Trope(+embedding)/Style/Edition rows. The existing ETL tests must
    still pass (behavior preserved).
  - **De-dup:** a discovery whose title+author already exists resolves to the existing Work (no
    new Work created); a new discovery creates one.
  - **`enrich_and_persist_work`** with a **mocked `ScoutManager`** (no real API) against the
    fixture seed: new title → persisted work-id; duplicate title → existing work-id.
  - **Pipeline assembly:** `create_recommendation_pipeline()` builds a `SequentialAgent` with the
    six steps in order; the `Targets`/`Discoveries` Pydantic `output_schema`s validate sample
    objects (and reject malformed ones) — a deterministic unit test, no API.
  - **Final extraction:** a pipeline run with faked step outputs yields
    `state["recommendation"]` (never "(no response)").
- **`api_dependent` (excluded from CI, manual):** the full live pipeline against a fixture-seeded
  DB asserts a non-empty, justified recommendation is produced and a `Suggestions` row is logged.
  Quota-heavy (Explorer + enrich + agents) — run manually.
- **Fixture seed:** a richer deterministic seed helper (Works/Tropes/Styles/ReadingHistory/
  Suggestions, reusing Spec 3's `trope_embeddings.json` real-vector approach) so e2e and
  integration tests are reproducible. Lives in a test helper, used by the `db_integration` tests.

## Files (anticipated)

- **Create:** `etl/persist.py` (extracted shared persist function);
  `agents/pipeline.py` (the `SequentialAgent` + custom `InternalCandidates`/`Enrichment`/`Logger`
  agents + `create_recommendation_pipeline()`); test files for each thread; a fixture-seed helper.
- **Modify:** `mcp/server.py` (`enrich_and_persist_work` tool); `orchestration/assets.py`
  (`vectorized_tropes` calls the shared persist function); `agents/runtime.py`
  (`run_recommendation` runs the pipeline and returns `state["recommendation"]`);
  `agents/services.py` (Analyst/Explorer get Pydantic `output_schema`s + `output_key`; the Critic
  agent is reused by the pipeline). `docs/project_notes/decisions.md` (an ADR for the pipeline
  architecture).
- The conversational Librarian path (`create_agent_mesh`, `LibrarianConversation`) is unchanged.

## Out of scope (→ Spec 5+)

Full SEC-001/002 hardening; real-data seeding of the full history (parallel op); a user-facing
CLI/REPL; the audiobook ETL coverage from REC-017.

## Success criteria

1. `run_recommendation(prompt)` runs the fixed-order `SequentialAgent` pipeline and returns a
   non-empty, Trope-RAG-justified recommendation — never "(no response)" — deterministically in
   ordering (no clarifying-question detour).
2. A web discovery not in the DB is de-duped, enriched, persisted as a Work with tropes +
   embeddings, and is rankable/justifiable by the Critic with DB-backed evidence.
3. The final recommendation is logged via `log_suggestion`.
4. `vectorized_tropes` and `enrich_and_persist_work` share one persist implementation; existing
   ETL tests still pass.
5. Deterministic tests gate in CI (`db_integration`, mocked scouts, fixture seed); the live e2e is
   `api_dependent` and excluded from CI; the offline suite stays green.
