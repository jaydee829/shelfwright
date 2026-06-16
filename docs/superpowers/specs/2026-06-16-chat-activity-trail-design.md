# Chat Activity Trail — Design (B1)

**Date:** 2026-06-16
**Status:** Approved (brainstormed), pending plan
**Origin:** Friends-and-family beta feedback, item B1 (see project memory `beta-feedback-triage`).

---

## 1. Background & Problem

During a chat turn the mesh can run for many seconds (Analyst → Critic → Explorer → enrichment),
but the operator couldn't tell what was happening: the assistant bubble sat **blank**, and the only
progress signal was a single status chip that **flickered with raw labels** ("Critic", a tool name)
and was cleared at the end. The operator saw "Critic" but couldn't tell the Explorer ran, and a lot
of the turn read as empty.

**Root cause (confirmed in code):** the activity data already flows end-to-end — each backend calls
`on_event(kind, detail)` (`agents/runtime.py:99-108` for ADK; `agents/backends/claude.py:_emit_block_event`
for Claude), `chat/stream.py` relays it as an SSE `activity {kind, detail}` event, and the client
forwards both to `onActivity(kind, detail)` (`frontend/src/api/client.ts:210`). The **frontend throws it
away**: `ChatView` ignores `kind`, stores `detail` in a single overwritten `activity` string, and renders
it as a transient chip while an empty assistant bubble shows for the whole run
(`frontend/src/views/ChatView.tsx`).

So B1 is a **presentation gap**, fixable almost entirely in the frontend.

## 2. Goals

- Show **which stage** the mesh is in, live, with friendly library-themed copy — not raw identifiers.
- The in-flight assistant bubble must **never look blank** (a pending state).
- Keep a **reviewable record** of the steps for a completed turn, without cluttering the thread.

## 3. Non-Goals (YAGNI)

- No backend / mesh changes — the events already carry everything needed.
- No persistence of activity steps. They are ephemeral (live-streamed). Reloaded past turns show the
  reply with no trail (unchanged from today). Persisting would need a schema change — out of scope.
- No token-level streaming of the reply (separate future work).
- No exhaustive trace of every tool/DB call (see granularity decision below).

## 4. Decisions (from brainstorm)

- **D1 — Granularity:** show **agent stages + slow tool milestones** (the ~minute enrichment, the
  library-candidate check). Other raw tool/DB calls are hidden.
- **D2 — Presentation:** a **collapsing step trail**. Steps accumulate as a live checklist (✓ done /
  ⟳ running) where the empty bubble is today; once the reply arrives the trail collapses into a small
  `▸ How I found these (N steps)` toggle above the answer. The live checklist doubles as the pending
  indicator.
- **D3 — Persistence:** **live-only**. The finished step list is kept on the in-session message object
  (so its toggle works), but is not persisted; reloaded older turns have no trail.
- **D4 — Labels:** each step maps to an **array of phrases**; the UI picks one at random each time the
  step fires (fresh per turn). Copy is **original, library/fantasy-themed homage** (light nods such as
  "Ook!", "L-space", "the stacks") — not quotations from any book.

## 5. Architecture (frontend-only)

### 5.1 `frontend/src/api/activityLabels.ts` (new)

A pure module that turns a raw `(kind, detail)` event into a display step, or `null` to hide it.

```
type StepKind = "stage" | "milestone"
interface StepLabel { phrases: string[]; stepKind: StepKind }

const STEP_LABELS: Record<string, StepLabel>   // keyed by detail.toLowerCase()
function labelForActivity(kind: string, detail: string): { text: string; stepKind: StepKind } | null
```

- Lookup is by `detail.toLowerCase()` so it covers ADK's `("tool","Explorer")` *and* Claude's
  `("agent","explorer")` uniformly (the incoming `kind` is not needed to classify; the map carries the
  `stepKind`).
- `labelForActivity` returns `null` for any key not in the map (raw DB/tool calls stay hidden), and
  otherwise returns a **randomly chosen** phrase from the pool plus its `stepKind`.
- Starter pools (approved; freely extensible — all original homage lines):
  - `analyst`: "Analyzing your tastes" · "Comparing tropes" · "Consulting the card catalog" ·
    "Cross-referencing your shelves" · "Reading the runes of your history" — stage
  - `explorer`: "Checking the stacks" · "Searching L-space" · "Wandering the infinite shelves" ·
    "Following a trail between worlds" · "Off to the far reading-rooms" — stage
  - `critic`: "Ranking matches" · "Weighing the contenders" · "Sorting wheat from chaff" ·
    "Judging covers and contents" — stage
  - `librarian`: "Ook!" · "Tending the collection" · "Consulting the Head Librarian" · "Shhh… working" — stage
  - `get_recommendation_candidates`: "Checking your recommendations" · "I think I have just the thing" ·
    "Pulling a few from the back room" · "Dusting off some candidates" — milestone
  - `enrich_and_persist_work`: "Cataloging a discovery" · "Filing it under the right tropes" ·
    "Enriching a new find" — milestone

### 5.2 `ChatView` state changes

Replace the single `activity` string with a per-turn step accumulator:

```
interface ActivityStep { id: number; text: string; stepKind: "stage" | "milestone"; status: "running" | "done" }
const [liveSteps, setLiveSteps] = useState<ActivityStep[]>([])
```

- `onActivity(kind, detail)`: `const label = labelForActivity(kind, detail)`. If `null`, ignore.
  Otherwise mark any current `running` step `done` and append the new step as `running`. **Dedupe**:
  if the new label text equals the current running step's text, do nothing (avoid repeats from
  multiple events of the same stage).
- `onText` / `onError`: mark the last step `done`.
- Turn completion: attach the finished `liveSteps` to the assistant message (see 5.3), then reset
  `liveSteps` to `[]`.

### 5.3 Local `ChatMessage` gains `steps`

The frontend `ChatMessage` type gets an optional `steps?: ActivityStep[]`. When a live turn completes,
its accumulated steps are written onto that assistant message so its collapsed toggle can render. Messages
loaded from the transcript have no `steps` (live-only, D3).

### 5.4 `ActivityTrail` component (new)

- **In-flight** (passed `liveSteps`, rendered where the empty assistant bubble is): a vertical checklist
  — each step shows ✓ when `done`, an animated ⟳ spinner when `running`. This is also the pending
  indicator, so the bubble never looks blank.
- **Completed** (passed a message's `steps`): a collapsed `▸ How I found these (N steps)` disclosure
  above the reply; expanding shows the same checklist (all ✓). Milestone steps may get a subtle marker
  but render in the same list.

## 6. Data Flow

```
backend on_event(kind, detail) -> SSE activity{kind,detail} -> client onActivity(kind, detail)
  -> ChatView: label = labelForActivity(kind, detail)
       null            -> ignore
       {text,stepKind} -> prior running step => done; append {text, running}; (dedupe consecutive)
  -> onText/onError    -> last step => done
  -> turn complete     -> assistantMessage.steps = liveSteps; liveSteps = []
ActivityTrail renders liveSteps (live checklist + pending) and, per finished message, the collapsed toggle.
```

## 7. Error Handling

- On the `error` SSE event the running step is marked `done` and the existing error message fills the
  assistant bubble; the trail collapses normally (no false "done", no orphan spinner).
- Client disconnect / abort mid-turn (existing behavior): the generator is cancelled; `liveSteps` is
  reset on unmount — no spinner is left running.
- An empty pool or unknown key never throws: unknown → `null` (hidden); pools are non-empty by
  construction (a unit test guards this).

## 8. Testing (vitest)

- `activityLabels`: known keys (both `("tool","Explorer")` and `("agent","explorer")`) return a phrase
  from the right pool with the right `stepKind`; unknown keys return `null`; every pool is non-empty;
  random pick is stable to test by stubbing `Math.random`.
- `ChatView`: driving an `onActivity` sequence accumulates ordered steps with running→done transitions;
  consecutive duplicate labels dedupe; unmapped tool calls are hidden; the live checklist shows while
  running (bubble not blank); after `onText` the trail collapses to the toggle and expands to the list;
  the `error` path closes the trail with the error message.
- Mind the vitest-4 `...Once` mock-leak rule and the `App.test.tsx` "mock every view" rule.

## 9. Files Touched

- `frontend/src/api/activityLabels.ts` (new) + test
- `frontend/src/views/ChatView.tsx` — step accumulator, completion handoff, pending state
- `frontend/src/views/ActivityTrail.tsx` (new) + `.css` (spinner, checklist, disclosure)
- `frontend/src/views/ChatView.css` — minor, if needed
- `frontend/src/api/client.ts` — add `steps?` to the local `ChatMessage` type (if the type lives there)
- `frontend/src/views/ChatView.test.tsx`, `frontend/src/api/activityLabels.test.ts`

## 10. Out of Scope / Future

- Persisting activity steps so reloaded turns keep their trail (needs a schema change).
- Token-level streaming of the reply text.
- More themed phrases / per-agent iconography — the pools are intentionally easy to extend.
- The other beta items: C1/C2 enrichment visibility + tropes-in-history, D1b history edit/delete,
  E1 dark mode (separate specs).
