# Chat Activity Trail — Implementation Plan (B1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single overwritten activity chip with a live, friendly, collapsing step trail so a chat turn shows which stage the mesh is in and never looks blank.

**Architecture:** Frontend-only. The backend already streams `activity {kind, detail}`; we add a label map (rotating themed phrase pools), accumulate ordered steps per turn in `ChatView`, and render an `ActivityTrail` (live checklist + collapsed "How I found these" toggle). Live-only — finished steps ride on the in-session message, never persisted.

**Tech Stack:** React 19 + TypeScript + Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-06-16-chat-activity-trail-design.md`

---

## Test commands (frontend, Windows host — NOT Docker)

Run from `C:\dev\agentic_librarian\frontend` via the PowerShell tool:
- Targeted test: `cd C:\dev\agentic_librarian\frontend; npx vitest run src/<path>`
- Full suite: `cd C:\dev\agentic_librarian\frontend; npx vitest run`
- Build (type-check): `cd C:\dev\agentic_librarian\frontend; npm run build`
- Lint: `cd C:\dev\agentic_librarian\frontend; npm run lint`

vitest-4 gotcha: use single-use `mockResolvedValueOnce`/`mockImplementationOnce` for per-test overrides. `App.test.tsx` mocks every view module — this plan adds no new view route, so no change there.

## File Structure

- `frontend/src/api/activityLabels.ts` (new) — themed phrase pools + `labelForActivity` + the `ActivityStep` type.
- `frontend/src/views/ActivityTrail.tsx` (new) + `ActivityTrail.css` (new) — presentational live + completed trail.
- `frontend/src/api/client.ts` — add optional `steps?: ActivityStep[]` to `ChatMessage`.
- `frontend/src/views/ChatView.tsx` — step accumulator, completion handoff, render the trail (replaces the chip).
- `frontend/src/views/ChatView.css` — drop the `.activity-chip` rule.
- Tests: `activityLabels.test.ts`, `ActivityTrail.test.tsx`, updated `ChatView.test.tsx`.

---

### Task 1: `activityLabels.ts` — themed phrase pools + mapper

**Files:** Create `frontend/src/api/activityLabels.ts`, `frontend/src/api/activityLabels.test.ts`.

- [ ] **Step 1: Write the failing test** `frontend/src/api/activityLabels.test.ts`

```ts
import { describe, expect, it, vi } from 'vitest'
import { _STEP_LABELS, labelForActivity } from './activityLabels'

describe('labelForActivity', () => {
  it('maps an ADK tool-named specialist and a Claude agent-named one to the same pool', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0) // first phrase
    expect(labelForActivity('tool', 'Explorer')).toEqual({ text: 'Checking the stacks', stepKind: 'stage' })
    expect(labelForActivity('agent', 'explorer')).toEqual({ text: 'Checking the stacks', stepKind: 'stage' })
    vi.restoreAllMocks()
  })

  it('marks slow tools as milestones, stages as stages', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    expect(labelForActivity('tool', 'enrich_and_persist_work')?.stepKind).toBe('milestone')
    expect(labelForActivity('tool', 'Analyst')?.stepKind).toBe('stage')
    vi.restoreAllMocks()
  })

  it('hides unmapped tool/DB calls and blanks', () => {
    expect(labelForActivity('tool', 'search_internal_database')).toBeNull()
    expect(labelForActivity('tool', '')).toBeNull()
  })

  it('every pool is non-empty', () => {
    for (const v of Object.values(_STEP_LABELS)) expect(v.phrases.length).toBeGreaterThan(0)
  })

  it('picks from the pool by random index', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.99) // last phrase
    const pool = _STEP_LABELS['analyst'].phrases
    expect(labelForActivity('tool', 'Analyst')?.text).toBe(pool[pool.length - 1])
    vi.restoreAllMocks()
  })
})
```

- [ ] **Step 2: Run it — expect failure**

`cd C:\dev\agentic_librarian\frontend; npx vitest run src/api/activityLabels.test.ts`
Expected: FAIL — cannot resolve `./activityLabels`.

- [ ] **Step 3: Implement** `frontend/src/api/activityLabels.ts`

```ts
export type StepKind = 'stage' | 'milestone'

export interface ActivityStep {
  id: number
  text: string
  stepKind: StepKind
  status: 'running' | 'done'
}

export interface ActivityLabel {
  text: string
  stepKind: StepKind
}

interface StepPool {
  phrases: string[]
  stepKind: StepKind
}

// Keyed by lower-cased activity `detail` so it covers ADK's ("tool","Explorer") and Claude's
// ("agent","explorer") uniformly. Unmapped details are hidden. All copy is original
// library/fantasy-themed homage (not quotations from any book).
const STEP_LABELS: Record<string, StepPool> = {
  analyst: {
    stepKind: 'stage',
    phrases: [
      'Analyzing your tastes',
      'Comparing tropes',
      'Consulting the card catalog',
      'Cross-referencing your shelves',
      'Reading the runes of your history',
    ],
  },
  explorer: {
    stepKind: 'stage',
    phrases: [
      'Checking the stacks',
      'Searching L-space',
      'Wandering the infinite shelves',
      'Following a trail between worlds',
      'Off to the far reading-rooms',
    ],
  },
  critic: {
    stepKind: 'stage',
    phrases: ['Ranking matches', 'Weighing the contenders', 'Sorting wheat from chaff', 'Judging covers and contents'],
  },
  librarian: {
    stepKind: 'stage',
    phrases: ['Ook!', 'Tending the collection', 'Consulting the Head Librarian', 'Shhh… working'],
  },
  get_recommendation_candidates: {
    stepKind: 'milestone',
    phrases: [
      'Checking your recommendations',
      'I think I have just the thing',
      'Pulling a few from the back room',
      'Dusting off some candidates',
    ],
  },
  enrich_and_persist_work: {
    stepKind: 'milestone',
    phrases: ['Cataloging a discovery', 'Filing it under the right tropes', 'Enriching a new find'],
  },
}

/** Map a raw (kind, detail) activity event to a display label, or null to hide it.
 *  `kind` is accepted for symmetry with the SSE callback; classification is by `detail`. */
export function labelForActivity(_kind: string, detail: string): ActivityLabel | null {
  const pool = STEP_LABELS[(detail || '').toLowerCase()]
  if (!pool) return null
  const text = pool.phrases[Math.floor(Math.random() * pool.phrases.length)]
  return { text, stepKind: pool.stepKind }
}

export const _STEP_LABELS = STEP_LABELS // exported for the non-empty-pool guard test
```

- [ ] **Step 4: Run it — expect pass**

`cd C:\dev\agentic_librarian\frontend; npx vitest run src/api/activityLabels.test.ts` → PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/activityLabels.ts frontend/src/api/activityLabels.test.ts
git commit -m "feat(chat): activityLabels - themed rotating phrase pools + mapper"
```

---

### Task 2: `ActivityTrail` component (live checklist + collapsed toggle)

**Files:** Create `frontend/src/views/ActivityTrail.tsx`, `frontend/src/views/ActivityTrail.css`, `frontend/src/views/ActivityTrail.test.tsx`.

- [ ] **Step 1: Write the failing test** `frontend/src/views/ActivityTrail.test.tsx`

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { CompletedActivityTrail, LiveActivityTrail } from './ActivityTrail'
import type { ActivityStep } from '../api/activityLabels'

const steps: ActivityStep[] = [
  { id: 1, text: 'Analyzing your tastes', stepKind: 'stage', status: 'done' },
  { id: 2, text: 'Checking the stacks', stepKind: 'stage', status: 'running' },
]

describe('ActivityTrail', () => {
  it('live trail shows every step', () => {
    render(<LiveActivityTrail steps={steps} />)
    expect(screen.getByText('Analyzing your tastes')).toBeInTheDocument()
    expect(screen.getByText('Checking the stacks')).toBeInTheDocument()
  })

  it('live trail renders nothing when empty', () => {
    const { container } = render(<LiveActivityTrail steps={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('completed trail starts collapsed, expands on click', async () => {
    render(<CompletedActivityTrail steps={steps} />)
    expect(screen.queryByText('Analyzing your tastes')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /how i found these \(2 steps\)/i }))
    expect(screen.getByText('Analyzing your tastes')).toBeInTheDocument()
  })

  it('completed trail renders nothing when empty', () => {
    const { container } = render(<CompletedActivityTrail steps={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
```

- [ ] **Step 2: Run it — expect failure**

`cd C:\dev\agentic_librarian\frontend; npx vitest run src/views/ActivityTrail.test.tsx` → FAIL (cannot resolve `./ActivityTrail`).

- [ ] **Step 3: Implement** `frontend/src/views/ActivityTrail.tsx`

```tsx
import { useState } from 'react'
import type { ActivityStep } from '../api/activityLabels'
import './ActivityTrail.css'

function StepRow({ step }: { step: ActivityStep }) {
  return (
    <div className={`trail-step ${step.stepKind}`}>
      <span className={`trail-mark ${step.status}`} aria-hidden>
        {step.status === 'done' ? '✓' : '⟳'}
      </span>
      <span className="trail-text">{step.text}</span>
    </div>
  )
}

/** In-flight trail: a live checklist that also serves as the pending indicator. */
export function LiveActivityTrail({ steps }: { steps: ActivityStep[] }) {
  if (steps.length === 0) return null
  return (
    <div className="activity-trail live" role="status" aria-live="polite">
      {steps.map((s) => (
        <StepRow key={s.id} step={s} />
      ))}
    </div>
  )
}

/** Completed trail on a finished assistant message: a collapsed disclosure above the reply. */
export function CompletedActivityTrail({ steps }: { steps: ActivityStep[] }) {
  const [open, setOpen] = useState(false)
  if (steps.length === 0) return null
  return (
    <div className="activity-trail done">
      <button className="trail-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? '▾' : '▸'} How I found these ({steps.length} step{steps.length === 1 ? '' : 's'})
      </button>
      {open && steps.map((s) => <StepRow key={s.id} step={s} />)}
    </div>
  )
}
```

`frontend/src/views/ActivityTrail.css`:

```css
.activity-trail { align-self: flex-start; display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: #6b7280; }
.activity-trail.done { margin: 2px 0; }
.trail-step { display: flex; align-items: center; gap: 6px; }
.trail-step.milestone .trail-text { font-style: italic; }
.trail-mark { display: inline-block; width: 1em; text-align: center; }
.trail-mark.running { animation: trail-spin 1s linear infinite; }
.trail-mark.done { color: #1f6f43; }
.trail-toggle { background: none; border: none; padding: 0; color: #6b7280; cursor: pointer; font-size: 13px; text-align: left; }
@keyframes trail-spin { to { transform: rotate(360deg); } }
```

- [ ] **Step 4: Run it — expect pass**

`cd C:\dev\agentic_librarian\frontend; npx vitest run src/views/ActivityTrail.test.tsx` → PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ActivityTrail.tsx frontend/src/views/ActivityTrail.css frontend/src/views/ActivityTrail.test.tsx
git commit -m "feat(chat): ActivityTrail - live checklist + collapsed disclosure"
```

---

### Task 3: Wire the trail into `ChatView`

**Files:** Modify `frontend/src/api/client.ts`, `frontend/src/views/ChatView.tsx`, `frontend/src/views/ChatView.css`, `frontend/src/views/ChatView.test.tsx`.

- [ ] **Step 1: Add failing tests** — APPEND to `frontend/src/views/ChatView.test.tsx` (keep existing tests):

```tsx
  it('shows a live trail step for a mapped agent, then a collapsed trail after the reply', async () => {
    vi.mocked(streamChat).mockImplementation(async (_msg: string, h: ChatHandlers) => {
      h.onActivity('tool', 'Explorer') // maps to an Explorer phrase (stage)
      h.onText('Try Dune.')
    })
    render(<ChatView />)
    await screen.findByPlaceholderText(/ask the librarian/i)
    await userEvent.type(screen.getByPlaceholderText(/ask the librarian/i), 'recommend a book')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(screen.getByText('Try Dune.')).toBeInTheDocument())
    // After the turn, the collapsed trail toggle is present (1 step recorded).
    expect(screen.getByRole('button', { name: /how i found these/i })).toBeInTheDocument()
  })

  it('hides unmapped tool calls (no trail recorded)', async () => {
    vi.mocked(streamChat).mockImplementation(async (_msg: string, h: ChatHandlers) => {
      h.onActivity('tool', 'search_internal_database') // unmapped -> hidden
      h.onText('Done.')
    })
    render(<ChatView />)
    await screen.findByPlaceholderText(/ask the librarian/i)
    await userEvent.type(screen.getByPlaceholderText(/ask the librarian/i), 'hi')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(screen.getByText('Done.')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /how i found these/i })).not.toBeInTheDocument()
  })
```

- [ ] **Step 2: Run — expect the 2 new tests FAIL** (no toggle rendered yet)

`cd C:\dev\agentic_librarian\frontend; npx vitest run src/views/ChatView.test.tsx`

- [ ] **Step 3a: Extend `ChatMessage`** in `frontend/src/api/client.ts` — add the import and the optional field:

At the top (with the other imports/types) ensure this import exists:
```ts
import type { ActivityStep } from './activityLabels'
```
Then in the `ChatMessage` interface add the field:
```ts
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  steps?: ActivityStep[]
}
```

- [ ] **Step 3b: Rewrite `frontend/src/views/ChatView.tsx`** to accumulate steps and render the trail:

```tsx
import { useEffect, useRef, useState } from 'react'
import { getCurrentConversation, newConversation, streamChat, type ChatMessage } from '../api/client'
import { labelForActivity, type ActivityStep } from '../api/activityLabels'
import { CompletedActivityTrail, LiveActivityTrail } from './ActivityTrail'
import './ChatView.css'

export default function ChatView() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [liveSteps, setLiveSteps] = useState<ActivityStep[]>([])
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const stepId = useRef(0)

  useEffect(() => {
    void getCurrentConversation().then((c) => setMessages(c.messages))
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [messages, liveSteps])

  async function send() {
    const text = input.trim()
    if (!text || sending) return
    setInput('')
    setSending(true)
    setLiveSteps([])
    stepId.current = 0
    let steps: ActivityStep[] = []
    // Append the user turn plus an in-flight assistant placeholder (empty content; not rendered
    // as a bubble until text arrives — the live trail is the pending indicator).
    setMessages((m) => [...m, { role: 'user', content: text }, { role: 'assistant', content: '' }])
    let reply = ''
    await streamChat(text, {
      onActivity: (kind, detail) => {
        const label = labelForActivity(kind, detail)
        if (!label) return
        const prev = steps[steps.length - 1]
        if (prev && prev.status === 'running') {
          if (prev.text === label.text) return // dedupe consecutive identical labels
          steps = [
            ...steps.slice(0, -1),
            { ...prev, status: 'done' },
            { id: ++stepId.current, text: label.text, stepKind: label.stepKind, status: 'running' },
          ]
        } else {
          steps = [...steps, { id: ++stepId.current, text: label.text, stepKind: label.stepKind, status: 'running' }]
        }
        setLiveSteps(steps)
      },
      onText: (chunk) => {
        reply += chunk
        setMessages((m) => [...m.slice(0, -1), { role: 'assistant', content: reply }])
      },
      onError: (detail) => {
        reply = reply || detail
        setMessages((m) => [...m.slice(0, -1), { role: 'assistant', content: reply }])
      },
    })
    // Finalize: mark the last running step done and attach the trail to the assistant message.
    steps = steps.map((s) => (s.status === 'running' ? { ...s, status: 'done' } : s))
    setMessages((m) => {
      const copy = [...m]
      const last = copy[copy.length - 1]
      if (last && last.role === 'assistant') copy[copy.length - 1] = { ...last, steps }
      return copy
    })
    setLiveSteps([])
    setSending(false)
  }

  async function startNew() {
    const c = await newConversation()
    setMessages(c.messages)
    setLiveSteps([])
  }

  return (
    <div className="chat">
      <div className="chat-toolbar">
        <button onClick={() => void startNew()} disabled={sending}>New chat</button>
      </div>
      <div className="chat-thread">
        {messages.map((m, i) => (
          <div key={i} className="msg-row">
            {m.role === 'assistant' && m.steps && m.steps.length > 0 && <CompletedActivityTrail steps={m.steps} />}
            {(m.content || m.role === 'user') && <div className={`bubble ${m.role}`}>{m.content}</div>}
          </div>
        ))}
        {sending && <LiveActivityTrail steps={liveSteps} />}
        <div ref={bottomRef} />
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault()
          void send()
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask the Librarian…"
          aria-label="Message"
        />
        <button type="submit" disabled={sending}>Send</button>
      </form>
    </div>
  )
}
```

- [ ] **Step 3c: Drop the dead `.activity-chip` rule** in `frontend/src/views/ChatView.css` (remove the line `.activity-chip { ... }`). Add a tiny wrapper rule so the msg-row groups the trail with its bubble:
```css
.msg-row { display: flex; flex-direction: column; gap: 4px; }
```

- [ ] **Step 4: Run the full ChatView suite + the trail/label suites — expect all pass**

`cd C:\dev\agentic_librarian; cd frontend; npx vitest run src/views/ChatView.test.tsx src/views/ActivityTrail.test.tsx src/api/activityLabels.test.ts`
Expected: PASS, including the 3 PRE-EXISTING ChatView tests (resume, send-and-stream, new-chat). If "sends a message and streams activity then reply" breaks: it calls `onActivity('search','Explorer is searching')` (unmapped → hidden), then `onText('Try Dune.')` — it only asserts the reply + user message, so it must still pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/views/ChatView.tsx frontend/src/views/ChatView.css frontend/src/views/ChatView.test.tsx
git commit -m "feat(chat): render the activity trail in ChatView (replaces the chip)"
```

---

### Task 4: Full frontend verification

- [ ] **Step 1: Full test suite** — `cd C:\dev\agentic_librarian\frontend; npx vitest run` → all files pass.
- [ ] **Step 2: Build (type-check)** — `cd C:\dev\agentic_librarian\frontend; npm run build` → succeeds (no TS errors; confirms `ActivityStep` import wiring across api/views).
- [ ] **Step 3: Lint** — `cd C:\dev\agentic_librarian\frontend; npm run lint` → clean. (The `★`-free file set; ensure no unused-var lint from the renamed state.)
- [ ] **Step 4: Finish** — use superpowers:finishing-a-development-branch (push + PR per the project's Gemini-review → squash-merge workflow).

---

## Self-Review

**Spec coverage:** D1 granularity (stages + milestones; unmapped hidden) → `labelForActivity` map + `null` (Task 1). D2 collapsing trail + pending → `LiveActivityTrail` (doubles as pending; empty in-flight bubble suppressed) + `CompletedActivityTrail` toggle (Tasks 2–3). D3 live-only → steps attached to the in-session message; transcript-loaded messages have no `steps`, so no trail (Task 3). D4 rotating themed pools, original homage copy, random pick → Task 1. Error path → `onError` falls through to finalize which marks steps done and attaches them; no orphan spinner (Task 3). Tests cover labels, component, and ChatView integration including the hidden-unmapped and post-reply-toggle cases.

**Placeholder scan:** none — every step has complete code/commands.

**Type consistency:** `ActivityStep` is defined once in `activityLabels.ts` and imported by `ActivityTrail.tsx`, `client.ts` (`ChatMessage.steps`), and `ChatView.tsx`. `labelForActivity` returns `{text, stepKind} | null`, consumed in `ChatView` as `label.text`/`label.stepKind`. `LiveActivityTrail`/`CompletedActivityTrail` both take `{ steps: ActivityStep[] }`.
