# Recommendation Novelty + Re-read Labels — Design (A1 + A3)

**Date:** 2026-06-15
**Status:** Approved (brainstormed), pending plan
**Origin:** Friends-and-family beta feedback, items A1 + A3 (see project memory `beta-feedback-triage`).
**Builds on:** PR #49 (`fix/beta-rec-count-and-librarian-history-check`) — default-3 recommendations
+ Librarian `check_reading_history`. **Sequencing: implement after #49 merges** (this work edits the
same `prompts.py` / `services.py` instructions); rebase the feature branch onto updated `main` first.

---

## 1. Background & Problem

On the live beta (ADK / Gemini backend), the Librarian's first recommendations were books the operator
had **already read**. They were valid re-read targets, but the operator wanted **at least one genuinely
new (unread) suggestion** in every set, and wanted each recommendation to **state whether it is a
re-read**.

**Root cause (confirmed in code):** `search_internal_database` (`mcp/server.py:81`) ranks the user's
catalog by trope/style similarity but **never filters by read status**. Because the catalog is built
*from* the user's reading history (Flow 1 ETL), internal candidates are overwhelmingly already-read.
`check_reading_history` (`mcp/server.py:273`) knows read status + re-read eligibility, but it is
per-`(title, author)` and the candidate-gathering path (`agents/candidates.py:extract_candidate_ids`)
never consults it. The Explorer (external discovery) is only invoked at the conversational Librarian's
discretion (`agents/services.py` inline instruction step 4), which `gemini-3.1-flash-lite` does not do
reliably.

A1 (guarantee a new pick) and A3 (label re-read vs new) both hinge on the same missing primitive:
**read-status awareness of each candidate.** They are therefore one design.

## 2. Goals

- **A1:** Every recommendation set contains **≥1 genuinely unread book**; the remaining picks may be
  eligible re-reads. The guarantee is **structural at the candidate-pool level** (deterministic code),
  not a prompt suggestion.
- **A3:** Every recommendation is labeled **New** or **Re-read** in both surfaces it appears:
  the chat reply and the Recommendations view cards.

## 3. Non-Goals (YAGNI)

- No rewrite of the Critic into structured (machine-validated) output — Approach B was considered and
  deferred. We can escalate to it later if telemetry shows agents still skip the unread pick.
- No change to vector-ranking quality / embeddings.
- No new "re-read age" threshold work: the **2-year** eligibility stays (the 5-year figure in feedback
  was a misremember). We only factor the existing rule into one reusable place.
- No change to the History / Add-book flows (those are separate beta items C/D).

## 4. Decisions (from brainstorm)

- **D1 — Novelty contract:** ≥1 new, rest may be re-reads.
- **D2 — New-pick source (tiered, cheapest-first):** reuse an unread book already in the catalog
  (e.g. a prior unacted suggestion) when one fits; only run the Explorer for a fresh external discovery
  when no unread candidate exists. Respects free-tier quota + latency.
- **D3 — Re-read eligibility:** unchanged at **> 2.0 years** since last completion (the existing
  `check_reading_history` rule, preserved exactly).
- **D4 — Too-recent reads (< 2y) are dropped** from the recommendation pool entirely — they are neither
  new nor valid re-reads, so we never suggest a book the user finished recently.
- **D5 — Label surfaces:** both the chat reply prose **and** the Recommendations view cards.
- **D6 — Enforcement approach:** A — a deterministic curation tool feeds read-status-tagged,
  novelty-balanced candidates to the agents; pool-level guarantee is structural, selection is guided.

## 5. Architecture

Three layers, smallest-to-largest blast radius:

### 5.1 Read-status primitive (`mcp/server.py`)

- `reread_eligibility(date_completed: date) -> tuple[bool, float]` — pure helper returning
  `(is_re_read_candidate, years_since)` using the single `years_since > 2.0` rule (preserving current
  behavior). `check_reading_history` is refactored to call it so the threshold has exactly one definition.
- `get_read_status(work_ids: list[str]) -> dict[str, dict]` — **batch** read-status by work id for the
  current user. One query: `ReadingHistory ⋈ Edition` filtered `Edition.work_id IN work_ids` and the
  user, taking the **latest** `date_completed` per work. Per-work value:
  `{"status": "Read"|"Unread", "last_read": "YYYY-MM-DD"|None, "years_since": float|None,
  "is_re_read_candidate": bool, "rating": int|None}`. Works absent from history → `Unread`.
  User-scoped via `get_required_user_id()` (fail-closed, ADR-048).

### 5.2 Candidate curation (`agents/candidates.py` + a tool in `mcp/server.py`)

- `curate_candidates(target_tropes, target_styles, limit=10) -> dict` (in `candidates.py`, backend-neutral,
  reuses existing `search_internal_database` + `get_unacted_suggestions` + the new `get_read_status`):
  1. Gather the union of `search_internal_database` and `get_unacted_suggestions` (the latter are prior
     **unread** picks), preserving similarity order, de-duplicated by work id.
  2. Annotate every candidate via `get_read_status`.
  3. **Partition:** `unread` (status Unread) → eligible as the "new" pick; `reread` (Read AND
     `is_re_read_candidate`) → eligible for the "rest" slots; **drop** Read-but-too-recent (D4).
  4. Order **unread first, then reread**; cap at `limit`.
  - Returns:
    ```json
    {
      "candidates": [
        {"id": "...", "title": "...", "authors": ["..."], "genres": [...],
         "description": "...", "read_status": "new" | "reread",
         "last_read": "YYYY-MM-DD" | null, "rating": 1-5 | null}
      ],
      "has_unread": true,
      "unread_count": 2,
      "reread_count": 5
    }
    ```
- `get_recommendation_candidates(target_tropes, target_styles, limit=10)` — thin MCP tool wrapping
  `curate_candidates`, exposed to both backends (ADK `FunctionTool`; Claude `_TOOL_SCHEMAS` entry in
  `agents/backends/claude_tools.py`). Becomes the **Critic's primary catalog entry point** (replacing
  its direct `search_internal_database` call) and is added to the **conversational Librarian's** tools
  so it sees `has_unread`.

### 5.3 Novelty enforcement (A1)

- **Pool-level (structural, deterministic):** `curate_candidates` always surfaces unread candidates
  first and reports `has_unread`. Whenever an unread catalog match exists, the agent is handed one
  ready to use.
- **Selection (guided):** Critic + Librarian instructions (both backends) updated to:
  *"From `get_recommendation_candidates`, recommend 3 (per #49's default) and ALWAYS include at least
  one candidate whose `read_status` is `new`. If `has_unread` is false, call the Explorer for a fresh
  discovery, enrich it, and use it as the new pick."*
- **One-shot pipeline (`agents/pipeline.py`):** the `InternalCandidates` step calls `curate_candidates`
  and writes the annotated candidates + `has_unread` into session state; the Explorer step already runs
  unconditionally there, so external unread candidates are always present. The Critic receives the same
  tags and the ≥1-new instruction.

### 5.4 Re-read labels (A3)

- **Chat prose:** the `read_status` / `last_read` tags come straight out of
  `get_recommendation_candidates`. Critic instruction adds: *"Tag each recommendation `[New]` or
  `[Re-read: last read YYYY]` using the candidate's `read_status`/`last_read`."* The model echoes
  provided data rather than inferring.
- **Rec-view cards (fully structural):** `GET /recommendations` (`api/recommendations.py`) computes
  read status for the suggested works at read time — a join against `ReadingHistory` for the suggestion
  work ids, user-scoped, reusing `reread_eligibility` — and adds `read_status` (`"new"|"reread"`),
  `last_read`, and `rating` to each payload item. `frontend/src/views/RecommendationsView.tsx` renders
  a badge: **"New"** or **"Re-read · 2019 · ★★★★"** (rating stars only when present).
  `frontend/src/api/client.ts` recommendation type gains the optional fields.

## 6. Data Flow (chat path, the live beta)

```
user vibe
  -> Analyst -> {tropes, styles}
  -> Librarian/Critic calls get_recommendation_candidates(tropes, styles)
       -> search_internal_database + get_unacted_suggestions  (union, similarity order)
       -> get_read_status(work_ids)                            (batch)
       -> partition: unread-first, drop <2y reads
       -> {candidates[...tagged...], has_unread}
  -> if has_unread == false: Librarian -> Explorer -> enrich_and_persist_work -> add as the new pick
  -> Critic ranks; final set = 3, >=1 read_status "new"; each tagged [New]/[Re-read: YYYY]
  -> log_suggestion(top pick); reply streamed
Rec view (later): GET /recommendations -> joins history -> each card carries read_status/last_read/rating -> badge
```

## 7. Error Handling

- Vector search returning nothing (e.g. an embedding hiccup) must not blank the pool: `curate_candidates`
  still returns the unacted-suggestion candidates; `has_unread` reflects whatever is available.
- `get_read_status` DB failure fails closed (raises) — consistent with the other user-scoped tools
  (ADR-048). A read-status lookup failing must not silently mislabel a read book as new.
- `/recommendations` history-join: a suggested work with no read row → `read_status: "new"` (correct).

## 8. Testing Strategy

**Unit (no DB):**
- `reread_eligibility` — boundary at exactly 2.0 years (just-under vs just-over).
- `get_read_status` — mixed read/unread/recent works → correct per-work fields (mock db_manager).
- `curate_candidates` — unread-first ordering; `has_unread`/counts; <2y reads dropped; empty-search
  degradation still returns unacted suggestions.
- Prompt guard tests — Critic & Librarian instructions require ≥1 `new`, the `[New]`/`[Re-read]` tag,
  use of `get_recommendation_candidates`, and Explorer-on-empty (`has_unread` false).
- Tool wiring — Critic & conversational Librarian expose `get_recommendation_candidates` on both
  backends (`test_agent_services.py`, `test_claude_backend.py`).

**Integration (DB):**
- `get_read_status` + `get_recommendation_candidates` against a seeded catalog with read + unread +
  recently-read works → correct partition and `has_unread`.
- `GET /recommendations` returns `read_status`/`last_read`/`rating` for a suggestion the user has read
  vs has not.

**Frontend (vitest):**
- `RecommendationsView` renders a "New" badge vs a "Re-read · YYYY · ★★★★" badge from the payload
  fields. (Remember the vitest-4 `...Once` mock-leak gotcha and the `App.test.tsx` view-mock rule.)

**Markers:** live `api_dependent` mesh tests stay deselected in the `-m "not api_dependent"` baseline.

## 9. Files Touched

- `src/agentic_librarian/mcp/server.py` — `reread_eligibility`, `get_read_status`,
  `get_recommendation_candidates`; refactor `check_reading_history`.
- `src/agentic_librarian/agents/candidates.py` — `curate_candidates`.
- `src/agentic_librarian/agents/prompts.py` — Critic + Librarian instruction updates (≥1-new, tagging,
  curated tool, Explorer-on-empty).
- `src/agentic_librarian/agents/services.py` — Critic uses `get_recommendation_candidates`; Librarian
  gains the tool; inline instruction updates.
- `src/agentic_librarian/agents/backends/claude_tools.py` — `_TOOL_SCHEMAS` entry for the new tool.
- `src/agentic_librarian/agents/pipeline.py` — `InternalCandidates` step uses curation + writes
  `has_unread`.
- `src/agentic_librarian/api/recommendations.py` — read-status on the `/recommendations` payload.
- `frontend/src/api/client.ts` — recommendation type fields.
- `frontend/src/views/RecommendationsView.tsx` (+ css/test) — badge.
- Tests across the above.

## 10. Out of Scope / Future

- Approach B (structured Critic output with code-validated novelty) — escalate only if needed.
- Surfacing *why* a re-read is being re-suggested (e.g. "you rated it ★★★★★ in 2018") beyond the badge.
- The other beta items: B1 chat activity log, C1/C2 enrichment visibility + tropes-in-history,
  D1b history edit/delete, E1 dark mode (separate specs).
