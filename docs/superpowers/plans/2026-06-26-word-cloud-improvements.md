# Word Cloud Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `flex-wrap` list-style word cloud with a compact, rotated, frequency-accentuated cloud built on `@isoterik/react-word-cloud`, with cloud-local text cleanup (split `/`, strip articles, merge+sum counts).

**Architecture:** Two pure helper modules (`wordCloudText.ts` for preprocessing, `wordCloudLayout.ts` for size/rotation/color math) feed a rewritten `WordCloud.tsx` that runs the d3-cloud layout via the library's `useWordCloud` hook and renders the SVG `<text>` itself (so palette/font/theme stay under our className+CSS control). The component is a drop-in: the `<WordCloud items={Ranked[]} />` contract is unchanged.

**Tech Stack:** React 19, TypeScript, Vite, Vitest 4 + Testing Library, `@isoterik/react-word-cloud` (d3-cloud), CSS-variable theming.

**Spec:** `docs/superpowers/specs/2026-06-26-word-cloud-improvements-design.md`

**Working directory:** All `npm`/`npx` commands run from `frontend/`. All file paths below are repo-relative.

**Context the engineer needs:**
- `Ranked` is `{ name: string; count: number }` (`frontend/src/api/client.ts`).
- `WordCloud` is used twice in `frontend/src/views/AnalysisView.tsx` (`top_tropes`, `style_cloud`) — do not change those call sites.
- The `--cat-1..6` palette and `--font-display` (`'Literata Variable', Georgia, serif`) tokens are defined in `frontend/src/index.css` for both themes.
- d3-cloud measures glyphs on a `<canvas>`, which jsdom cannot do, so the layout never completes in tests. Tests therefore **mock the hook** and assert the component's `role="img"` aria-label, never positioned text. Real visual proof is the QC harness (Task 5).

---

### Task 1: Add the `@isoterik/react-word-cloud` dependency

**Files:**
- Modify: `frontend/package.json` (and `frontend/package-lock.json` if present)

- [ ] **Step 1: Install the package**

Run (from `frontend/`):
```bash
npm install @isoterik/react-word-cloud@^1.3.0
```

- [ ] **Step 2: Verify it resolves and is React-19 compatible**

Run:
```bash
npm ls @isoterik/react-word-cloud
```
Expected: prints `@isoterik/react-word-cloud@1.3.x` with no `UNMET PEER DEPENDENCY` warnings (peer dep is `react ^18 || ^19`; the repo is on React 19).

- [ ] **Step 3: Confirm the hook import type-checks**

Create a scratch check by running the type-checker on the existing project (no source change yet):
```bash
npx tsc -b
```
Expected: exits 0 (the package ships its own types; this confirms the install didn't break the build).

- [ ] **Step 4: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "build(frontend): add @isoterik/react-word-cloud for the word cloud"
```

---

### Task 2: `wordCloudText.ts` — cloud-local text preprocessing (pure, TDD)

**Files:**
- Create: `frontend/src/components/wordCloudText.ts`
- Test: `frontend/src/components/wordCloudText.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/wordCloudText.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { prepareCloudWords } from './wordCloudText'

describe('prepareCloudWords', () => {
  it('strips a single leading article', () => {
    expect(prepareCloudWords([{ name: 'The Seer', count: 5 }])).toEqual([{ name: 'Seer', count: 5 }])
    expect(prepareCloudWords([{ name: 'A Court of Thorns', count: 2 }])).toEqual([
      { name: 'Court of Thorns', count: 2 },
    ])
  })

  it('does not strip an article mid-word', () => {
    expect(prepareCloudWords([{ name: 'Theseus', count: 1 }])).toEqual([{ name: 'Theseus', count: 1 }])
  })

  it('splits on "/" and keeps both sides', () => {
    const out = prepareCloudWords([{ name: 'Enemies / Lovers', count: 4 }])
    expect(out).toEqual([
      { name: 'Enemies', count: 4 },
      { name: 'Lovers', count: 4 },
    ])
  })

  it('merges case-insensitive duplicates summing counts', () => {
    const out = prepareCloudWords([
      { name: 'Enemies / Lovers', count: 4 },
      { name: 'lovers', count: 3 },
    ])
    expect(out).toEqual([
      { name: 'Lovers', count: 7 },
      { name: 'Enemies', count: 4 },
    ])
  })

  it('sorts by count descending', () => {
    const out = prepareCloudWords([
      { name: 'Rare', count: 1 },
      { name: 'Common', count: 9 },
    ])
    expect(out.map((w) => w.name)).toEqual(['Common', 'Rare'])
  })

  it('collapses internal whitespace and trims', () => {
    expect(prepareCloudWords([{ name: '  Slow   Burn  ', count: 2 }])).toEqual([
      { name: 'Slow Burn', count: 2 },
    ])
  })

  it('drops empty and article-only parts', () => {
    expect(prepareCloudWords([{ name: 'The / Seer', count: 5 }])).toEqual([{ name: 'Seer', count: 5 }])
    expect(prepareCloudWords([{ name: ' / ', count: 5 }])).toEqual([])
  })

  it('returns [] for empty input', () => {
    expect(prepareCloudWords([])).toEqual([])
  })

  it('does not mutate the input objects', () => {
    const input = [{ name: 'Found Family', count: 3 }]
    prepareCloudWords(input)
    expect(input).toEqual([{ name: 'Found Family', count: 3 }])
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
npx vitest run src/components/wordCloudText.test.ts
```
Expected: FAIL — `prepareCloudWords` is not exported / module not found.

- [ ] **Step 3: Implement the module**

Create `frontend/src/components/wordCloudText.ts`:
```ts
import type { Ranked } from '../api/client'

const LEADING_ARTICLE = /^(the|a|an)\s+/i
const ARTICLE_ONLY = /^(the|a|an)$/i

/** Cloud-local preprocessing for trope/style labels. Splits '/'-joined names,
 * strips a single leading article, collapses whitespace, drops empty/article-only
 * parts, then merges case-insensitive duplicates summing their counts. The full
 * names are preserved everywhere else in the app. Sorted by count desc. */
export function prepareCloudWords(items: Ranked[]): Ranked[] {
  const merged = new Map<string, Ranked>()
  for (const item of items) {
    for (const rawPart of item.name.split('/')) {
      const cleaned = rawPart.trim().replace(/\s+/g, ' ').replace(LEADING_ARTICLE, '').trim()
      if (!cleaned || ARTICLE_ONLY.test(cleaned)) continue
      const key = cleaned.toLowerCase()
      const existing = merged.get(key)
      if (existing) existing.count += item.count
      else merged.set(key, { name: cleaned, count: item.count })
    }
  }
  return [...merged.values()].sort((a, b) => b.count - a.count)
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
npx vitest run src/components/wordCloudText.test.ts
```
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/wordCloudText.ts frontend/src/components/wordCloudText.test.ts
git commit -m "feat(frontend): cloud-local word preprocessing (split, strip articles, merge)"
```

---

### Task 3: `wordCloudLayout.ts` — size / rotation / color math (pure, TDD)

**Files:**
- Create: `frontend/src/components/wordCloudLayout.ts`
- Test: `frontend/src/components/wordCloudLayout.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/wordCloudLayout.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { MIN_PX, colorClass, hashString, maxPx, mulberry32, rotateFor, sizeFor } from './wordCloudLayout'

describe('maxPx (width-responsive ceiling)', () => {
  it('shrinks on narrow widths and caps on wide ones', () => {
    expect(maxPx(200)).toBe(36) // floor
    expect(maxPx(375)).toBe(41) // round(375*0.11)=41
    expect(maxPx(1280)).toBe(60) // cap
  })
})

describe('sizeFor (power curve)', () => {
  it('puts the least frequent at the floor and the most frequent at the responsive max', () => {
    expect(sizeFor(2, 2, 20, 1280)).toBeCloseTo(MIN_PX) // norm 0 -> MIN
    expect(sizeFor(20, 2, 20, 1280)).toBeCloseTo(60) // norm 1 -> maxPx(1280)
  })

  it('returns the midpoint when all counts are equal', () => {
    const mid = MIN_PX + 0.5 ** 1.4 * (maxPx(1280) - MIN_PX)
    expect(sizeFor(7, 7, 7, 1280)).toBeCloseTo(mid)
  })

  it('scales the max down on a narrow column', () => {
    expect(sizeFor(20, 2, 20, 375)).toBeCloseTo(41)
  })
})

describe('rotateFor (deterministic ~70/30)', () => {
  it('is deterministic and only ever 0 or 90', () => {
    for (const t of ['Found Family', 'Slow Burn', 'Seer', 'Lovers']) {
      const r = rotateFor(t)
      expect([0, 90]).toContain(r)
      expect(rotateFor(t)).toBe(r)
    }
  })
})

describe('hashString', () => {
  it('is deterministic and non-negative', () => {
    expect(hashString('abc')).toBe(hashString('abc'))
    expect(hashString('abc')).toBeGreaterThanOrEqual(0)
  })
})

describe('colorClass', () => {
  it('cycles cat-1..6', () => {
    expect(colorClass(0)).toBe('cat-1')
    expect(colorClass(5)).toBe('cat-6')
    expect(colorClass(6)).toBe('cat-1')
  })
})

describe('mulberry32', () => {
  it('is a deterministic PRNG in [0, 1)', () => {
    const a = mulberry32(1337)
    const b = mulberry32(1337)
    const first = a()
    expect(first).toBe(b())
    expect(first).toBeGreaterThanOrEqual(0)
    expect(first).toBeLessThan(1)
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
npx vitest run src/components/wordCloudLayout.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the module**

Create `frontend/src/components/wordCloudLayout.ts`:
```ts
export const MIN_PX = 14
export const EXP = 1.4
export const LARGE_PX = 34 // size at/above which a word also gets bold weight

const MAX_FACTOR = 0.11
const MAX_FLOOR = 36
const MAX_CEIL = 60

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n))

/** Largest font size, derived from container width so it shrinks on mobile. */
export function maxPx(width: number): number {
  return clamp(Math.round(width * MAX_FACTOR), MAX_FLOOR, MAX_CEIL)
}

/** Frequency -> px via a power curve with a legible floor and width-responsive cap. */
export function sizeFor(count: number, lo: number, hi: number, width: number): number {
  const norm = hi === lo ? 0.5 : (count - lo) / (hi - lo)
  return MIN_PX + norm ** EXP * (maxPx(width) - MIN_PX)
}

/** Deterministic 32-bit FNV-1a-style string hash. */
export function hashString(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/** ~30% of words land vertical (90 deg), deterministically per word text. */
export function rotateFor(text: string): 0 | 90 {
  return hashString(text) % 10 < 3 ? 90 : 0
}

/** Cycles the categorical palette so every word (incl. the smallest) stays readable. */
export function colorClass(index: number): string {
  return `cat-${(index % 6) + 1}`
}

/** Seedable PRNG so the d3-cloud layout is stable across re-renders. */
export function mulberry32(seed: number): () => number {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
npx vitest run src/components/wordCloudLayout.test.ts
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/wordCloudLayout.ts frontend/src/components/wordCloudLayout.test.ts
git commit -m "feat(frontend): word cloud size/rotation/color math helpers"
```

---

### Task 4: Rewrite `WordCloud.tsx` + CSS, update the two affected test files

**Files:**
- Modify (rewrite): `frontend/src/components/WordCloud.tsx`
- Modify (rewrite): `frontend/src/components/WordCloud.css`
- Modify (rewrite): `frontend/src/components/WordCloud.test.tsx`
- Modify: `frontend/src/views/AnalysisView.test.tsx`

- [ ] **Step 1: Rewrite the component test (failing first)**

Replace the entire contents of `frontend/src/components/WordCloud.test.tsx` with:
```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// d3-cloud needs a real <canvas>; jsdom can't lay out, so mock the hook and
// assert the accessible summary instead of positioned text.
vi.mock('@isoterik/react-word-cloud', () => ({
  useWordCloud: () => ({ computedWords: [], isLoading: false }),
}))

import WordCloud from './WordCloud'

describe('WordCloud', () => {
  it('exposes an accessible summary of the preprocessed words', () => {
    render(
      <WordCloud
        items={[
          { name: 'The Seer', count: 10 },
          { name: 'Enemies / Lovers', count: 6 },
        ]}
      />,
    )
    const label = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(label).toContain('Seer')
    expect(label).toContain('Enemies')
    expect(label).toContain('Lovers')
    expect(label).not.toContain('The Seer')
  })

  it('reports the merged word count (split + summed duplicates)', () => {
    render(
      <WordCloud
        items={[
          { name: 'A / B', count: 5 },
          { name: 'B', count: 2 },
        ]}
      />,
    )
    // 'A' (5) and 'B' (5+2) -> 2 distinct words
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('Word cloud of 2 words')
  })

  it('renders nothing when empty', () => {
    const { container } = render(<WordCloud items={[]} />)
    expect(container.firstChild).toBeNull()
  })
})
```

- [ ] **Step 2: Run the component test to verify it fails**

Run:
```bash
npx vitest run src/components/WordCloud.test.tsx
```
Expected: FAIL — the current component renders a `<ul>` with no `role="img"`/aria-label.

- [ ] **Step 3: Rewrite the component**

Replace the entire contents of `frontend/src/components/WordCloud.tsx` with:
```tsx
import { useEffect, useMemo, useRef, useState } from 'react'
import { useWordCloud } from '@isoterik/react-word-cloud'
import type { Ranked } from '../api/client'
import { prepareCloudWords } from './wordCloudText'
import { LARGE_PX, colorClass, mulberry32, rotateFor, sizeFor } from './wordCloudLayout'
import './WordCloud.css'

const FONT = "'Literata Variable', Georgia, serif"
const ASPECT = 0.6
const SEED = 1337
const DEFAULT_WIDTH = 600

/** A compact, rotated, frequency-accentuated word cloud. Runs the d3-cloud
 * "Wordle" layout via useWordCloud and renders the SVG <text> itself so color
 * (--cat-* palette), font, and light/dark theming stay under CSS control.
 * Shared by the trope cloud and the style cloud. */
export default function WordCloud({ items }: { items: Ranked[] }) {
  const words = useMemo(() => prepareCloudWords(items), [items])
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(DEFAULT_WIDTH)
  const random = useMemo(() => mulberry32(SEED), [])

  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w && w > 0) setWidth(w)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const counts = words.map((w) => w.count)
  const lo = counts.length ? Math.min(...counts) : 0
  const hi = counts.length ? Math.max(...counts) : 0
  const height = Math.round(width * ASPECT)

  const { computedWords } = useWordCloud({
    words: words.map((w) => ({ text: w.name, value: w.count })),
    width,
    height,
    font: FONT,
    fontWeight: 'normal',
    fontStyle: 'normal',
    fontSize: (word) => sizeFor(word.value, lo, hi, width),
    rotate: (word) => rotateFor(word.text),
    padding: 1,
    spiral: 'archimedean',
    random,
  })

  if (words.length === 0) return null

  const colorByText = new Map(words.map((w, i) => [w.name, colorClass(i)]))
  const top = words.slice(0, 3).map((w) => w.name).join(', ')
  const label = `Word cloud of ${words.length} word${words.length === 1 ? '' : 's'}. Most frequent: ${top}.`

  return (
    <div className="word-cloud" ref={ref} role="img" aria-label={label}>
      <svg width={width} height={height} aria-hidden="true">
        <g transform={`translate(${width / 2},${height / 2})`}>
          {computedWords.map((w) => (
            <text
              key={w.text}
              className={`${colorByText.get(w.text) ?? 'cat-1'}${w.size >= LARGE_PX ? ' lg' : ''}`}
              textAnchor="middle"
              transform={`translate(${w.x},${w.y}) rotate(${w.rotate})`}
              style={{ fontSize: `${w.size}px`, fontFamily: FONT }}
            >
              {w.text}
            </text>
          ))}
        </g>
      </svg>
    </div>
  )
}
```

- [ ] **Step 4: Rewrite the CSS**

Replace the entire contents of `frontend/src/components/WordCloud.css` with:
```css
.word-cloud {
  width: 100%;
  display: flex;
  justify-content: center;
}
.word-cloud svg {
  max-width: 100%;
  height: auto;
}
/* Font MUST match the FONT string passed to useWordCloud, or measurement and
   render diverge and words overlap. */
.word-cloud text {
  font-family: var(--font-display);
  cursor: default;
}
.word-cloud text.lg { font-weight: 600; }
.word-cloud text.cat-1 { fill: var(--cat-1); }
.word-cloud text.cat-2 { fill: var(--cat-2); }
.word-cloud text.cat-3 { fill: var(--cat-3); }
.word-cloud text.cat-4 { fill: var(--cat-4); }
.word-cloud text.cat-5 { fill: var(--cat-5); }
.word-cloud text.cat-6 { fill: var(--cat-6); }
```

- [ ] **Step 5: Run the component test to verify it passes**

Run:
```bash
npx vitest run src/components/WordCloud.test.tsx
```
Expected: PASS (3 tests).

- [ ] **Step 6: Update `AnalysisView.test.tsx` for the new aria-label rendering**

The two `getByText` assertions for cloud words no longer hold (words render as SVG `<text>` only after a real layout). In `frontend/src/views/AnalysisView.test.tsx`:

First, add a hook mock directly under the existing `vi.mock('../api/client', …)` line (line 5):
```tsx
vi.mock('@isoterik/react-word-cloud', () => ({
  useWordCloud: () => ({ computedWords: [], isLoading: false }),
}))
```

Then replace these two lines in the first test:
```tsx
    expect(screen.getByText('Chosen One')).toBeInTheDocument()
    expect(screen.getByText('Atmospheric')).toBeInTheDocument()
```
with:
```tsx
    expect(screen.getByRole('img', { name: /chosen one/i })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /atmospheric/i })).toBeInTheDocument()
```

- [ ] **Step 7: Run the full frontend suite, lint, and type-check**

Run:
```bash
npm test
npm run lint
npx tsc -b
```
Expected: all green. (`npm test` runs the whole vitest suite; confirm `WordCloud`, `AnalysisView`, and `App` tests all pass.)

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/WordCloud.tsx frontend/src/components/WordCloud.css frontend/src/components/WordCloud.test.tsx frontend/src/views/AnalysisView.test.tsx
git commit -m "feat(frontend): packed, rotated, frequency-accentuated word cloud (#83)"
```

---

### Task 5: Visual QC at mobile + desktop, both themes (verification + tuning)

**Files:**
- Possibly modify (tuning only): `frontend/src/components/wordCloudLayout.ts`
- Local-only (git-excluded, do not commit): `frontend/qc.tsx`, `frontend/qc.html`, `frontend/qc-shot.mjs`

This task verifies the real rendered output (the unit tests can't, since jsdom has no canvas) and tunes the layout constants. Reference: `docs/frontend-visual-qc.md`.

- [ ] **Step 1: Enrich the QC analysis fixture to exercise preprocessing**

In `frontend/qc.tsx`, replace the `top_tropes` and `style_cloud` arrays in the `analysis` fixture with longer lists that include `/`-joined and article-prefixed names and a wide count spread, so packing, splitting, rotation, and size contrast are all visible:
```tsx
  top_tropes: [
    { name: 'Found Family', count: 29 }, { name: 'Enemies / Lovers', count: 24 },
    { name: 'Morally Grey', count: 21 }, { name: 'The Chosen One', count: 18 },
    { name: 'Slow Burn', count: 17 }, { name: 'Hidden Identity', count: 14 },
    { name: 'Quest', count: 12 }, { name: 'Mentor / Protege', count: 10 },
    { name: 'Forbidden Love', count: 9 }, { name: 'A Court Intrigue', count: 7 },
    { name: 'Redemption Arc', count: 6 }, { name: 'Found Family', count: 5 },
    { name: 'Unreliable Narrator', count: 4 }, { name: 'Time Loop', count: 3 },
    { name: 'Heist', count: 2 },
  ],
```
```tsx
  style_cloud: [
    { name: 'Atmospheric', count: 22 }, { name: 'Lyrical', count: 14 },
    { name: 'First / Third Person', count: 12 }, { name: 'Cynical', count: 9 },
    { name: 'Minimalist', count: 7 }, { name: 'Unreliable', count: 5 },
    { name: 'Wry', count: 4 }, { name: 'Naturalistic', count: 3 },
    { name: 'Ornate', count: 2 },
  ],
```

- [ ] **Step 2: Start the dev server**

Run (from `frontend/`, in the background):
```bash
npm run dev
```
Expected: Vite serves at `http://localhost:5173`. The harness is at `http://localhost:5173/qc.html`.

- [ ] **Step 3: Screenshot the Analysis view at both viewports and themes**

Use the Playwright shot script (`frontend/qc-shot.mjs` per `docs/frontend-visual-qc.md`) to capture `/qc.html` navigated to Analysis, for the matrix:
- viewport 375×800 (mobile) — light theme and dark theme
- viewport 1280×900 (desktop) — light theme and dark theme

Read the four PNGs back and inspect:
1. Words are **packed** (no list-like line layout), with ~30% vertical.
2. Size contrast is clear; the biggest word **pops** but does not overflow the mobile column; the smallest word stays legible (≥14px).
3. All `--cat-*` colors appear and are readable in both themes.
4. `/`-joined names are split (e.g. `Enemies`, `Lovers` separate; no `Enemies / Lovers`); `The Chosen One` shows as `Chosen One`.
5. No console errors.

- [ ] **Step 4: Tune constants if needed**

If the mobile max is too large/small or contrast is off, adjust `MAX_FACTOR`/`MAX_FLOOR`/`MAX_CEIL`/`EXP`/`MIN_PX`/`LARGE_PX` in `frontend/src/components/wordCloudLayout.ts`, re-run the layout unit tests (`npx vitest run src/components/wordCloudLayout.test.ts` — update the expected numbers if you changed a constant) and re-screenshot until both viewports look right.

- [ ] **Step 5: Stop the dev server and commit any tuning**

Stop the background `npm run dev`. Revert the local QC fixture edits to `qc.tsx` if desired (it is git-excluded either way). If you changed any layout constant:
```bash
git add frontend/src/components/wordCloudLayout.ts frontend/src/components/wordCloudLayout.test.ts
git commit -m "fix(frontend): tune word cloud sizing for mobile + desktop (#83)"
```
If no tuning was needed, there is nothing to commit for this task.

---

## Self-Review

**Spec coverage:**
- Library choice (`@isoterik/react-word-cloud`, hook-level) → Tasks 1, 4. ✓
- Split `/` + strip articles + merge-sum (cloud-local) → Task 2. ✓
- Power-curve sizing, width-responsive max, ~70/30 rotation, palette → Task 3. ✓
- Drop-in component, hook render, className/CSS theming, seeded layout, font-match → Task 4. ✓
- `role="img"` aria-summary + jsdom degrade via mocked hook → Task 4 tests. ✓
- Empty → null → Task 4 test. ✓
- QC both themes + **mobile and desktop** viewports → Task 5. ✓
- Dependency add → Task 1. ✓

**Placeholder scan:** No TBD/"handle edge cases"/"similar to" — every code step has complete code and exact commands. ✓

**Type consistency:** `Ranked` (`{name,count}`) used uniformly; `prepareCloudWords(Ranked[]): Ranked[]`; `sizeFor(count, lo, hi, width)`, `rotateFor(text)`, `colorClass(index)`, `maxPx(width)`, `mulberry32(seed)`, `hashString(s)` signatures match between Task 3's definitions and Task 4's calls; `FONT` string matches `var(--font-display)` value used in `WordCloud.css`. ✓
