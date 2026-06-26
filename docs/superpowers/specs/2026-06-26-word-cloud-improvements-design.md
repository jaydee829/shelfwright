# Word Cloud Improvements (GH #83) — Design

**Status:** Approved (brainstorm) → ready for implementation plan
**Date:** 2026-06-26
**Issue:** [#83 — Word Cloud improvements](https://github.com/jaydee829/agentic_librarian/issues/83)
**Scope:** Frontend only. No backend, schema, or API changes.

## 1. Problem

The current `WordCloud` (shipped in the Analysis viz upgrade, PR #74) is a
`flex-wrap` list: words are sorted largest→smallest and laid out in justified
lines with generous gaps. It reads like a ranked list, not a cloud — too much
whitespace, no packing, no rotation. Issue #83 asks for: tighter compaction,
rotations (e.g. 90°), accentuated size contrast, and splitting/cleanup of the
wordier and `/`-joined trope/style labels (e.g. `The Seer` → `Seer`,
`Enemies / Lovers` → `Enemies` + `Lovers`) — **for the cloud only**, preserving
the full nuanced names everywhere else (rec chips, analysis lists, etc.).

## 2. Approach

Build on **`@isoterik/react-word-cloud`** (v1.3.0; React 19 peer dep; scoped d3
deps — `d3-cloud` + `d3-scale` + `d3-scale-chromatic`, not full d3). Use its
low-level **`useWordCloud` hook**, which runs the d3-cloud "Wordle" layout
(Archimedean-spiral placement, sprite-based collision packing, rotation) and
returns `computedWords` with `{ text, x, y, rotate, size, ... }`. We render the
SVG `<text>` ourselves.

Why the hook rather than the library's high-level `<WordCloud>` component:
- Full control of the `--cat-*` palette + Literata font + light/dark theme
  reactivity via our established **className + CSS** pattern (same reason
  Recharts is themed by className — SVG `fill` can't resolve `var()` reliably as
  a presentation attribute).
- The `isLoading`/empty state gives us the jsdom-degrade hook for the
  `role="img"` aria-summary test pattern we already use for unrenderable charts.
- A seeded `random` makes the layout **deterministic** (no reshuffle on
  re-render).

Rejected alternatives:
- **`adorable-word-cloud`** — pre-1.0 (v0.1.2), untouched since Aug 2024, and
  hard-depends on **all of d3** (`d3 ^7.9.0`) with no custom-render escape hatch.
- **raw `d3-cloud`** — same engine, but we'd hand-write the React glue, async
  handling, and types that `react-word-cloud` already provides.
- **Python word-cloud packages** — generate a static raster image; breaks the
  client-side JSON data flow and gives no light/dark theme reactivity.

## 3. Architecture (two units, drop-in)

Public contract is unchanged — `<WordCloud items={Ranked[]} />` — so
`AnalysisView` and both call sites (`top_tropes`, `style_cloud`) are untouched.

### 3.1 `frontend/src/components/wordCloudText.ts` (new — pure)

```ts
import type { Ranked } from '../api/client'

export function prepareCloudWords(items: Ranked[]): Ranked[]
```

Pure data transform, no React/DOM. For each input item:
1. Split `name` on `/` (any surrounding whitespace) into parts.
2. For each part: trim, strip a single leading article (`/^(the|a|an)\s+/i`),
   collapse internal runs of whitespace to one space.
3. Drop parts that are empty after cleaning.
4. Accumulate into a map keyed by the **lowercased** cleaned text, **summing
   `count`** across duplicates (cross-item and from splitting). The display name
   is the first-seen cleaned form (preserves original casing).
5. Return the merged words sorted by `count` desc.

This is the only place the "/" split, article stripping, and count-summing live;
it is cloud-local and does not touch shared data.

### 3.2 `frontend/src/components/WordCloud.tsx` (rewrite — visual)

- Props unchanged: `{ items: Ranked[] }`.
- `const words = prepareCloudWords(items)`; if empty → return `null`.
- Container `ref` + `ResizeObserver` provides width; height is a fixed aspect
  (`Math.round(width * ASPECT)`, `ASPECT = 0.6`), with a sensible initial
  fallback (`width 600`) before the ref measures.
- Map words to the hook's `Word[]` shape (`{ text, value: count }`) and call
  `useWordCloud({ words, width, height, font, fontSize, rotate, padding,
  random, spiral })`.
- Render: an outer `<div className="word-cloud" ref role="img" aria-label=…>`
  containing an `<svg>` with a centered `<g transform="translate(w/2,h/2)">`;
  map `computedWords` to `<text>` (see §4). While `isLoading` (or no computed
  words — e.g. jsdom), render only the accessible label, no `<text>`.

### 3.3 `frontend/src/components/WordCloud.css` (rewrite)

- `.word-cloud { … }` container sizing (width 100%, the fixed-aspect height).
- `text.cat-1 { fill: var(--cat-1); } … text.cat-6 { fill: var(--cat-6); }`
  → theme-reactive color, no inline fills.
- `text { font-family: var(--font-display); }` — **must** match the `font`
  string passed to the hook (see §4) or measurement vs. render diverge.
- A weight rule for large words (e.g. `text.lg { font-weight: 600 }`), applied
  when a word's size exceeds a threshold.

## 4. Layout parameters (tunable in QC)

These are starting values; final tuning happens against real data in the QC
harness (§7).

- **Font (measurement + render):** `FONT = "'Literata Variable', Georgia, serif"`
  — passed to the hook as `font` *and* set in CSS on `text`. Identical strings.
- **Size (power curve, width-responsive max):** with `lo`/`hi` = min/max merged
  count, `norm = hi === lo ? 0.5 : (count - lo) / (hi - lo)`, then
  `size = MIN_PX + norm ** EXP * (MAX_PX - MIN_PX)`.
  `MIN_PX = 14`, `EXP = 1.4`. **`MAX_PX` is derived from the container width**, not
  a fixed constant, so it shrinks on narrow/mobile columns:
  `MAX_PX = clamp(round(width * MAX_FACTOR), MAX_FLOOR, MAX_CEIL)` with
  `MAX_FACTOR = 0.11`, `MAX_FLOOR = 36`, `MAX_CEIL = 60`. (≈360px mobile → ~40px;
  ≳545px → caps at 60px.) The floor keeps the largest word impactful even on
  narrow screens; the cap prevents one word from dominating a wide layout.
  `MAX_FACTOR`/floor/cap are tuned in the QC harness at both viewports.
- **Rotation (deterministic, ~70/30):** `rotate(word) = hashString(word.text)
  % 10 < 3 ? 90 : 0`. A stable string hash so a given word always orients the
  same way; ~30% land vertical.
- **Color:** word at sorted index `idx` → `className = "cat-" + ((idx % 6) + 1)`.
  Large words (`size` past a threshold, e.g. > 34px) also get `lg` for weight.
- **Packing:** `padding = 1`, `spiral = "archimedean"`, `random =` a seeded
  mulberry32 (fixed seed) so layout is stable across renders.

## 5. Accessibility & degrade

- The container is `role="img"` with an `aria-label` **always present**,
  summarizing the cloud, e.g.
  `"Word cloud of 12 tropes. Most frequent: Found Family, Slow Burn, Seer."`
  (count of merged words + top 3 by count). This is both the a11y story and the
  test hook.
- d3-cloud measures glyphs on a `<canvas>`; jsdom has no real canvas, so the
  layout does not complete in tests → `computedWords` is empty and we render the
  label only. No overlap risk, no thrown errors.
- Empty `items` → `null` (unchanged contract; both call sites already guard,
  and the style-cloud section is already conditionally rendered).
- All-equal counts (`hi === lo`) → every word at mid size (`norm = 0.5`).

## 6. Data flow

`AnalysisView` → `<WordCloud items={data.top_tropes | data.style_cloud} />`
(unchanged) → `prepareCloudWords` → `useWordCloud` → `computedWords` → custom
SVG `<text>` render. Both clouds reuse the same component, as today.

## 7. Testing

- **`wordCloudText.test.ts`** (pure, runs locally): split on `/`; strip leading
  `The`/`A`/`An` (case-insensitive, leading-only — `A Court of Thorns` → `Court
  of Thorns`, but `Banana` is untouched); collapse whitespace; merge
  case-insensitive duplicates summing counts (`Enemies / Lovers` + a separate
  `Lovers` → one `Lovers` with summed count); both sides of a split kept; sort
  desc; edge cases (empty input, multiple slashes, a part that is only an
  article, leading/trailing whitespace).
- **`WordCloud.test.tsx`**: asserts the `role="img"` aria-label reflects
  **preprocessed** names (`The Seer` → `Seer`; `A / B` → both `A` and `B`) and
  the merged word count; returns `null` on empty `items`. Does **not** assert on
  positioned `<text>` (d3-cloud doesn't lay out in jsdom) — the visual is proven
  in QC.
- **QC harness** (`frontend/qc.html` / `qc.tsx` + Playwright): screenshot the
  trope cloud and style cloud in **both themes** at **two viewports — mobile
  (~375px) and full desktop (~1280px)**. Verify packing density, rotation mix,
  size contrast, palette spread, and small-word legibility, and specifically
  confirm the width-responsive `MAX_PX` keeps the largest word from overflowing
  or dominating the narrow mobile column while still reaching ~60px on desktop.
  This is the real quality gate; tune §4 params here. Zero console errors.

## 8. Dependency

Add `@isoterik/react-word-cloud` to `frontend/package.json` dependencies
(transitively pulls scoped `d3-*` + `react-fast-compare` +
`@floating-ui/react-dom` — small). React 19 compatible.

## 9. Out of scope

- Tooltips and entrance animation (the library offers both; off by default for
  determinism and YAGNI).
- Any backend / API / schema change; the proportion bar, style radar, and
  genre/mood bars; global normalization of trope/style names (cleanup here is
  cloud-local — see GH #72 for the DB-level `Style.name` canonicalization).

## 10. Coordination

Frontend-only; per the coordination board the only other active work is the
Safari-auth fix (auth/backend) and the backend bench — **no file overlap** with
`components/WordCloud.*` + the new `wordCloudText.ts`. No cross-team request
needed.
