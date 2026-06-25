# Analysis Viz Upgrade — Design

**Date:** 2026-06-25
**GitHub:** #13 (Phase 4: Web Interface and Analysis)
**Status:** Approved design → implementation plan next
**Owner:** design-work worktree, branch `feat/analysis-viz` (frontend **and** backend, operator-authorized)

---

## 1. Goal

Turn the Analysis page from six ranked `<ul>` lists into a single scrolling **"story of your reading"**: a snapshot, the **shape** of your taste (a style radar), your narrative **fingerprint** (trope cloud), your genre/mood **mix** (charts), your style **flavor** (style cloud), and the **people** behind it. Introduce **Recharts** for the charted sections.

This is one cohesive frontend feature plus **one small, additive backend change** (style scoring on the existing `/analysis` endpoint). No schema changes, no migration.

## 2. Non-goals (deferred, with issues)

- **Per-author style-radar comparison** (overlay top authors) → **#71**. v1 ships only the single aggregate radar.
- **DB-level `Style.name` canonicalization** (the trope-name treatment) → **#72**. v1 title-cases for display at the aggregation layer, which is sufficient for a clean cloud.
- **"Why this rec" highlight** (#68) — unrelated, not in scope.
- Narrator *performance* styles are **not** on the radar or style cloud (different category; the People section keeps narrators as a ranked list).

## 3. Page composition (single scroll, top → bottom)

1. **Snapshot** — keep the 4 stat tiles (`total_read`, `read_this_year`, `average_rating`, `distinct_authors`); add a **format proportion bar** from `snapshot.formats` (§7.1).
2. **Style radar (hero)** — "The shape of your reading." 8 binned axes. (§5)
3. **Trope cloud** — narrative fingerprint, from `top_tropes`. (§6)
4. **Genre & mood** — two horizontal **bar charts** (Recharts `BarChart`) from `genres` / `moods`; genre rows labeled with the existing `GenreIcon`.
5. **Style cloud** — the nominal style flavor words. (§6)
6. **People** — Authors & Narrators stay ranked lists (cheap, readable).

The existing 4-tab structure (`Tab` state, `TABS`) is **retired**. Each section is its own component so the view file stays focused.

## 4. Style data model (background for §5–§6)

Style lives in `Style` (`name`, `category`, 1536-d `embedding`) linked through `AuthorStyle` / `NarratorStyle` / `WorkStyle`, each carrying an `attribute_type`. Author style is a 9-attribute **baseline** per author; work style records work-specific attributes (`perspective`, `interiority`, `thematic_depth`) plus any **differences** from the author baseline (the work scout omits attributes identical to baseline).

**Per read-work style resolution:** for a given attribute, use the work's `WorkStyle` override if present, else fall back to the work's author's `AuthorStyle` baseline. Aggregate **at the work level** across the user's reading history (so authors you read more often weigh more — correct for "your taste").

### Attribute routing

| `attribute_type` | Destination | Notes |
|---|---|---|
| `pacing` | Radar: **Pace** | author baseline, work may override |
| `prose_density` | Radar: **Density** | |
| `thematic_depth` | Radar: **Depth** | work-level |
| `interiority` | Radar: **Inner focus** | work-level |
| `humor` | Radar: **Humor** | |
| `emotional_distance` | Radar: **Warmth** | anchors oriented so high = warm |
| `lexicon` | Radar: **Lexicon** | |
| `world_building` | Radar: **World-building** | |
| `tone` | Style cloud | nominal |
| `style` | Style cloud | nominal (prose style: lyrical/minimalist/…) |
| `dialogue_style` | Style cloud | nominal |
| `perspective` | Style cloud | nominal (1st/3rd/omniscient/unreliable) |

## 5. The style radar

- **Subject:** one aggregate shape over everything the user has read (option A).
- **Axes (8, fixed order):** Pace · Density · Depth · Inner focus · Humor · Warmth · Lexicon · World-building.

### Binning — embedding-projection onto bipolar anchors

Each axis defines a **low-anchor** and **high-anchor** phrase. A style value is scored by projecting its (already-stored) embedding onto the low→high direction:

```
axis_dir = normalize(e_high - e_low)
score(v) = clamp( dot(e_v - e_low, axis_dir) / norm(e_high - e_low), 0, 1 )
```

So the low anchor scores ~0, the high anchor ~1, and any value (even an unseen scout phrase) lands between. The 16 anchor embeddings are computed once and cached (module-level, same `get_cached_embedding` path the scouts use). No per-request embedding API calls — style embeddings already exist on each `Style` row; scoring is cheap vector math.

**Anchor phrases (low ↔ high):**

| Axis | Low anchor | High anchor |
|---|---|---|
| Pace | "slow-burn, languid, meandering pace" | "breakneck, fast-paced, propulsive pace" |
| Density | "spare, minimalist, sparse prose" | "dense, ornate, flowery prose" |
| Depth | "light, breezy entertainment" | "heavy, philosophical, weighty themes" |
| Inner focus | "external, plot-driven, action-focused" | "deeply introspective, interior character thoughts" |
| Humor | "serious, humorless, grave" | "constantly comedic, funny, humorous" |
| Warmth | "clinical, detached, emotionally cold" | "intimate, warm, emotionally close" |
| Lexicon | "plain, simple, accessible vocabulary" | "archaic, academic, specialized vocabulary" |
| World-building | "minimal, sparse world-building" | "immersive, richly detailed world-building" |

*(Fallback if projection reads noisy in practice: a curated phrase→score lookup per axis. Not chosen for v1 — projection needs zero upkeep as the catalog grows.)*

### Aggregation

For each axis: collect the resolved value per read work that has one, score each, **average**. Result is `0–1` or `null` (no work on the shelf carries that attribute).

### Degrade

- An axis with `null` (no data) is dropped from the radar.
- If fewer than ~3 axes have data, hide the radar entirely and show only the clouds (the view tolerates a missing/short `style_radar`).

## 6. The clouds

A single `WordCloud` component: entries rendered at a font size scaled by `count` (clamped to a readable min/max), wrapping flow layout (no physics/packing lib). **Frequency is encoded by size and weight only — never by fading.** Each word takes a full-strength color cycled from the categorical palette (§7.1) by index, so adjacent words differ and *every* word, including the smallest, stays readable. (The earlier gold-with-opacity approach made small words illegible — explicitly rejected.)

Fed two datasets:
- **Trope cloud** ← `top_tropes` (already in payload).
- **Style cloud** ← new `style_cloud` (nominal styles, §4). Names **title-cased at aggregation** so case/punctuation duplicates merge into one count.

## 7. Charts / Recharts

Recharts (new dependency) carries the genre & mood **bars** (`BarChart`) and the **radar** (`RadarChart`). Both themed via the Arcane CSS variables (gilt fills, ink grid lines) read through CSS custom properties; wrapped in `ResponsiveContainer`; each gets an `aria-label` summarizing its data for accessibility. The genre/mood bars stay single-hue gilt — the row labels already differentiate them, and bars on a common axis read cleanly in one color.

The **format proportion bar** (§7.1) and the **word clouds** (§6) are *not* Recharts — they're lightweight hand-rolled markup, and both use the categorical palette below.

### 7.1 Color system — categorical palette

The donut was rejected (humans judge length on a common baseline far better than angle/area), so the format mix becomes a single **horizontal 100%-stacked proportion bar**: one full-width bar, segments ordered largest → smallest, each a distinct categorical color, with a legend (swatch · name · %) underneath as the source of truth. A %-label renders inside a segment only when it's wide enough and contrast passes; otherwise it lives in the legend only. Every segment keeps a minimum visible width so small formats never vanish.

Both the proportion bar and the word clouds draw from a dedicated **categorical scale** — distinct *hues* from across the palette, not shades of one. Add these tokens to `index.css` for both themes (values reuse existing palette hues so they stay on-brand and readable in each theme):

```css
:root {                /* light */
  --cat-1: #c79a3e;  /* gold (gilt)   */  --cat-2: #1f9e94;  /* teal (spine)  */
  --cat-3: #6d4ed6;  /* violet (glow) */  --cat-4: #9a3b2e;  /* brick (accent)*/
  --cat-5: #3f7d4f;  /* green (ok)    */  --cat-6: #b07d2a;  /* deep amber    */
}
:root[data-theme="dark"] {
  --cat-1: #e3b85e;  --cat-2: #45e0d0;  --cat-3: #b9a6ff;
  --cat-4: #f08a7e;  --cat-5: #79c089;  --cat-6: #e0a44a;
}
```

Six categories cover formats (usually ≤4) and give the clouds enough variety; if a dataset exceeds six entries the scale cycles. Consumers reference `--cat-1..6` (never hardcoded hex).

## 8. API contract (the backend change)

Two **additive** fields on the existing `GET /analysis` response (single round trip, consistent with today's design). No new endpoint, no schema, no migration.

```jsonc
{
  // ...existing snapshot, genres, moods, top_tropes, authors, narrators...
  "style_radar": {
    "pace": 0.72, "density": 0.40, "depth": 0.65, "inner_focus": 0.55,
    "humor": 0.20, "warmth": 0.68, "lexicon": 0.50, "world_building": 0.80
  },                                  // each value 0–1 or null
  "style_cloud": [ { "name": "Atmospheric", "count": 14 }, ... ]  // title-cased, ranked
}
```

Frontend `Analysis` type gains `style_radar?` and `style_cloud?` (both optional) so the view **degrades gracefully** if the fields are absent — the radar shows "Gathering your style…" and the style cloud is hidden until the backend lands. This mirrors how `GenreIcon` shipped ahead of the `recommendations.genres` field.

## 9. File structure

**Backend (design-work holds; reserved on the coordination board):**
- `src/agentic_librarian/api/analysis.py` — add `style_radar` + `style_cloud` to the response; resolve per-work style + route attributes.
- `src/agentic_librarian/api/analysis_style.py` *(new)* — anchor definitions, cached anchor embeddings, `score_axis()` projection, aggregation helpers. Keeps the scoring logic out of the route.
- `test/unit/test_analysis_style.py` *(new)* — projection ordering, anchor calibration (low ~0, high ~1), title-case merge.
- `test/integration/test_analysis_api*.py` — endpoint returns the two fields over a fixture shelf; degrade when no style data.

**Frontend (no overlap with bench):**
- `frontend/src/views/AnalysisView.{tsx,css}` — rewrite to the single-scroll composition; one child component per section.
- `frontend/src/components/StyleRadar.{tsx,css}` *(new)* — Recharts `RadarChart` wrapper + degrade states.
- `frontend/src/components/WordCloud.{tsx,css}` *(new)* — shared by trope + style clouds.
- `frontend/src/components/StatChart.{tsx,css}` *(new, or inline)* — the genre/mood bars (thin Recharts `BarChart` wrappers).
- `frontend/src/components/ProportionBar.{tsx,css}` *(new)* — the format proportion bar (hand-rolled flex segments + legend).
- `frontend/src/index.css` — add the `--cat-1..6` categorical tokens for both themes (§7.1).
- `frontend/src/api/client.ts` — `style_radar?` / `style_cloud?` on `Analysis`.
- `frontend/package.json` — add `recharts`.

## 10. Error handling & edge cases

- **Empty shelf:** existing "No data yet" copy; radar + clouds hidden.
- **Partial style data:** axes drop individually; radar hides under ~3 axes (§5).
- **Backend not yet deployed:** optional fields absent → graceful degrade (§8).
- **Anchor embedding fetch fails at startup:** scoring returns `null` for all axes (radar hidden), never 500s the endpoint.
- **Theming in dark mode:** all viz colors (Recharts fills, `--cat-*` segments/words) come from CSS variables, verified in both themes via the headless QC harness.

## 11. Testing

- **Backend:** unit tests for `score_axis` (known phrase pairs produce expected ordering; anchors calibrate to ~0/~1), aggregation over a fixture reading history, title-case merge in the cloud; db_integration for the endpoint shape + degrade path.
- **Frontend:** Vitest + Testing Library against mocked `/analysis` — radar present, radar absent/degraded, empty shelf, both clouds. Reuse the headless QC harness (`qc.html`/`qc.tsx` + Playwright) for both light/dark themes.

## 12. Coordination

Operator-authorized: design-work implements both sides on `feat/analysis-viz`. The coordination board reserves `api/analysis.py` + `api/analysis_style.py` so the backend bench avoids them until merge. Change is read-only/additive (no migration) → low blast radius. PR follows the repo convention (Gemini review, squash-merge `(#N)`).
