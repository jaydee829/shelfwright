# Analysis Viz Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Analysis page into a single scrolling dashboard — snapshot + format proportion bar, a style radar (8 embedding-projected axes), trope & style word clouds, genre/mood bars, and people — backed by two additive `/analysis` fields.

**Architecture:** Backend adds a self-contained scoring module (`api/analysis_style.py`) that bins categorical style values to 0–1 by projecting each `Style.embedding` onto per-axis bipolar anchor phrases, plus a nominal-style cloud counter; the existing `/analysis` route loads style relationships and returns `style_radar` + `style_cloud` (additive, no schema/migration). Frontend consumes them with graceful degrade, rendering Recharts (radar, bars) + hand-rolled proportion bar and word clouds, all themed via a new `--cat-1..6` categorical palette.

**Tech Stack:** FastAPI + SQLAlchemy + pgvector (backend); React 19 + Vite + TypeScript + Recharts + Vitest/Testing Library (frontend). Spec: `docs/superpowers/specs/2026-06-25-analysis-viz-upgrade-design.md`.

**Conventions for every task:**
- Run from the worktree root `C:\dev\agentic_librarian\.claude\worktrees\design-work` on branch `feat/analysis-viz`. Do NOT touch `main`.
- Python lint/format before each backend commit: `uvx ruff@0.15.16 check --fix .` then `uvx ruff@0.15.16 format .` (CRLF noise is normal on Windows; only real diffs commit).
- Frontend tests: `cd frontend; npm test`. Lint: `cd frontend; npm run lint`.
- `db_integration`-marked tests run in CI against Postgres; locally they need the DB (skip if unavailable, they still run in CI).
- Backend files `api/analysis.py` + `api/analysis_style.py` are reserved for this branch on the coordination board — they are ours to edit.

---

### Task 1: Frontend foundation — Recharts, categorical tokens, payload types

**Files:**
- Modify: `frontend/package.json` (add `recharts`)
- Modify: `frontend/src/index.css:5-36` (light `:root`) and `:38-56` (dark) — add `--cat-1..6`
- Modify: `frontend/src/api/client.ts:36-49` — extend `Analysis`
- Test: `frontend/src/index.theme.test.ts` (token presence)

- [ ] **Step 1: Install Recharts**

Run: `cd frontend; npm install recharts`
Expected: `recharts` appears under `dependencies` in `frontend/package.json`, lockfile updated.

- [ ] **Step 2: Add the categorical palette tokens**

In `frontend/src/index.css`, inside the light `:root` block (after the `--gilt`/`--star` line, line ~10) add:

```css
  /* categorical scale — distinct hues for proportion bar + word clouds */
  --cat-1: #c79a3e; --cat-2: #1f9e94; --cat-3: #6d4ed6;
  --cat-4: #9a3b2e; --cat-5: #3f7d4f; --cat-6: #b07d2a;
```

Inside the `:root[data-theme="dark"]` block (after its `--gilt`/`--star` line, ~line 43) add:

```css
  --cat-1: #e3b85e; --cat-2: #45e0d0; --cat-3: #b9a6ff;
  --cat-4: #f08a7e; --cat-5: #79c089; --cat-6: #e0a44a;
```

- [ ] **Step 3: Write the failing token test**

Append to `frontend/src/index.theme.test.ts` a test asserting the categorical tokens are defined in both themes. Match the existing file's style (read it first for the harness it uses — it parses `index.css`). If the file reads the raw CSS text, assert:

```ts
it('defines the categorical palette in both themes', () => {
  const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8')
  for (let i = 1; i <= 6; i++) expect(css).toContain(`--cat-${i}:`)
  // dark overrides present (six in light + six in dark = twelve declarations)
  expect(css.match(/--cat-1:/g)?.length).toBe(2)
})
```

If `index.theme.test.ts` uses a different mechanism (e.g. jsdom getComputedStyle), follow that mechanism instead — keep the assertion (six tokens, overridden in dark).

Run: `cd frontend; npm test -- index.theme`
Expected: PASS (tokens were added in Step 2). If it FAILS, fix the token declarations.

- [ ] **Step 4: Extend the `Analysis` type**

In `frontend/src/api/client.ts`, add an exported axis type and extend `Analysis` (after the `narrators` field, line ~48):

```ts
export type StyleAxis =
  | 'pace' | 'density' | 'depth' | 'inner_focus'
  | 'humor' | 'warmth' | 'lexicon' | 'world_building'

export type StyleRadar = Record<StyleAxis, number | null>
```

And inside `interface Analysis`, add two optional fields so the view degrades when the backend hasn't shipped them:

```ts
  style_radar?: StyleRadar
  style_cloud?: Ranked[]
```

- [ ] **Step 5: Verify build + full test run**

Run: `cd frontend; npm run lint; npm test`
Expected: lint clean; all existing tests pass (the type change is additive; `AnalysisView.test.tsx` is rewritten in Task 8 and still passes against the current view until then).

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/index.css frontend/src/api/client.ts frontend/src/index.theme.test.ts
git commit -m "feat(analysis): recharts dep, categorical palette tokens, style payload types"
```

---

### Task 2: Backend style-scoring module

Self-contained module: axis definitions, anchor phrases, the projection scorer, and the two aggregation helpers. All logic is unit-tested with injected vectors/embedder — no DB, no API.

**Files:**
- Create: `src/agentic_librarian/api/analysis_style.py`
- Test: `test/unit/test_analysis_style.py`

- [ ] **Step 1: Write the module skeleton with axis/anchor/routing constants**

Create `src/agentic_librarian/api/analysis_style.py`:

```python
"""Style scoring for the Analysis radar + style cloud (GH #13).

Bins categorical style values to a 0-1 magnitude per axis by projecting each
``Style.embedding`` onto a per-axis bipolar anchor pair (low phrase -> high
phrase). Anchors are embedded once in the same space as the stored style
vectors (gemini-embedding-001, 1536-d, SEMANTIC_SIMILARITY -- see
scouts/utils.get_cached_embedding) and cached. Pure vector math otherwise.

Nominal style attributes (tone, prose style, dialogue, perspective) have no
single axis; they feed the style word cloud instead.
"""

from __future__ import annotations

import math
import os
from collections import Counter
from collections.abc import Callable
from typing import Protocol

from agentic_librarian.scouts.utils import get_cached_embedding

# Fixed display order of the radar axes.
AXES: tuple[str, ...] = (
    "pace", "density", "depth", "inner_focus",
    "humor", "warmth", "lexicon", "world_building",
)

# attribute_type (from AuthorStyle/WorkStyle) -> radar axis.
RADAR_ATTR_TO_AXIS: dict[str, str] = {
    "pacing": "pace",
    "prose_density": "density",
    "thematic_depth": "depth",
    "interiority": "inner_focus",
    "humor": "humor",
    "emotional_distance": "warmth",
    "lexicon": "lexicon",
    "world_building": "world_building",
}

# Nominal attribute_types -> the style cloud (no axis).
CLOUD_ATTRS: frozenset[str] = frozenset({"tone", "style", "dialogue_style", "perspective"})

# Bipolar anchor phrases per axis: (low pole, high pole). High = the "more" end.
ANCHORS: dict[str, tuple[str, str]] = {
    "pace": ("slow-burn, languid, meandering pace", "breakneck, fast-paced, propulsive pace"),
    "density": ("spare, minimalist, sparse prose", "dense, ornate, flowery prose"),
    "depth": ("light, breezy entertainment", "heavy, philosophical, weighty themes"),
    "inner_focus": ("external, plot-driven, action-focused", "deeply introspective, interior character thoughts"),
    "humor": ("serious, humorless, grave", "constantly comedic, funny, humorous"),
    "warmth": ("clinical, detached, emotionally cold", "intimate, warm, emotionally close"),
    "lexicon": ("plain, simple, accessible vocabulary", "archaic, academic, specialized vocabulary"),
    "world_building": ("minimal, sparse world-building", "immersive, richly detailed world-building"),
}

_EMBED_MODEL = "gemini-embedding-001"  # must match StyleManager / stored Style vectors
```

- [ ] **Step 2: Write failing tests for `score_axis` projection**

Create `test/unit/test_analysis_style.py`:

```python
from agentic_librarian.api import analysis_style as m


def test_score_axis_low_anchor_is_zero():
    low = [0.0, 0.0]
    high = [1.0, 0.0]
    assert m.score_axis(low, low, high) == 0.0


def test_score_axis_high_anchor_is_one():
    low = [0.0, 0.0]
    high = [1.0, 0.0]
    assert m.score_axis(high, low, high) == 1.0


def test_score_axis_midpoint_is_half():
    low = [0.0, 0.0]
    high = [2.0, 0.0]
    assert m.score_axis([1.0, 0.0], low, high) == 0.5


def test_score_axis_clamps_below_zero_and_above_one():
    low = [0.0, 0.0]
    high = [1.0, 0.0]
    assert m.score_axis([-3.0, 0.0], low, high) == 0.0
    assert m.score_axis([5.0, 0.0], low, high) == 1.0


def test_score_axis_degenerate_anchor_returns_none():
    assert m.score_axis([1.0, 1.0], [0.5, 0.5], [0.5, 0.5]) is None
```

Run: `python -m pytest test/unit/test_analysis_style.py -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'score_axis'`.

- [ ] **Step 3: Implement `score_axis`**

Append to `analysis_style.py`:

```python
def _dot(a: list[float], b: list[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b, strict=False))


def score_axis(value_vec: list[float], low_vec: list[float], high_vec: list[float]) -> float | None:
    """Project ``value_vec`` onto the low->high direction. low -> 0, high -> 1, clamped.

    Returns None if the anchors are degenerate (identical), which would make the
    axis undefined.
    """
    direction = [h - lo for h, lo in zip(high_vec, low_vec, strict=False)]
    denom = _dot(direction, direction)
    if denom == 0.0:
        return None
    offset = [v - lo for v, lo in zip(value_vec, low_vec, strict=False)]
    t = _dot(offset, direction) / denom
    return max(0.0, min(1.0, t))
```

Run: `python -m pytest test/unit/test_analysis_style.py -q`
Expected: PASS (5 tests).

- [ ] **Step 4: Write failing tests for the aggregation helpers**

The aggregation helpers operate on **style maps** — one per read work — where each map is `dict[attribute_type, StyleLike]` and `StyleLike` has `.name: str` and `.embedding: list[float] | None`. Add a tiny stub + tests to `test/unit/test_analysis_style.py`:

```python
from dataclasses import dataclass


@dataclass
class _Style:
    name: str
    embedding: list[float] | None


def _fake_embed_factory():
    """Embed anchors so 'high'/'more' phrases map near [1,0] and 'low' near [0,0]."""
    def embed(text: str) -> list[float]:
        hot = any(w in text for w in ("fast", "dense", "heavy", "interior", "comedic",
                                      "intimate", "archaic", "immersive"))
        return [1.0, 0.0] if hot else [0.0, 0.0]
    return embed


def test_aggregate_radar_averages_scored_axes():
    embed = _fake_embed_factory()
    fast = _Style("fast-paced", [1.0, 0.0])   # near high anchor -> ~1
    slow = _Style("slow-burn", [0.0, 0.0])    # near low anchor  -> ~0
    maps = [{"pacing": fast}, {"pacing": slow}]
    radar = m.aggregate_radar(maps, embed)
    assert radar["pace"] == 0.5                # mean of 1 and 0
    assert radar["humor"] is None             # no data on this axis


def test_aggregate_radar_no_embedder_is_all_none():
    fast = _Style("fast-paced", [1.0, 0.0])
    radar = m.aggregate_radar([{"pacing": fast}], None)
    assert set(radar) == set(m.AXES)
    assert all(v is None for v in radar.values())


def test_aggregate_radar_skips_styles_without_embedding():
    embed = _fake_embed_factory()
    radar = m.aggregate_radar([{"pacing": _Style("fast-paced", None)}], embed)
    assert radar["pace"] is None


def test_aggregate_cloud_counts_titlecased_nominal_styles():
    maps = [
        {"tone": _Style("atmospheric", None), "perspective": _Style("first person", None)},
        {"tone": _Style("ATMOSPHERIC", None)},   # merges case-insensitively after titlecase
        {"pacing": _Style("fast-paced", None)},  # radar attr -> NOT in cloud
    ]
    cloud = m.aggregate_cloud(maps)
    counts = {row["name"]: row["count"] for row in cloud}
    assert counts == {"Atmospheric": 2, "First Person": 1}
```

Run: `python -m pytest test/unit/test_analysis_style.py -q`
Expected: FAIL (`aggregate_radar` / `aggregate_cloud` missing).

- [ ] **Step 5: Implement the embedder, anchor cache, and aggregation helpers**

Append to `analysis_style.py`:

```python
class _StyleLike(Protocol):
    name: str
    embedding: list[float] | None


# Module-level anchor cache: axis -> (low_vec, high_vec). Filled once per process.
_anchor_cache: dict[str, tuple[list[float], list[float]]] = {}
_genai_client = None


def get_anchor_vectors(embed: Callable[[str], list[float]]) -> dict[str, tuple[list[float], list[float]]]:
    """Embed each axis's anchor pair once and memoize."""
    if not _anchor_cache:
        for axis, (low, high) in ANCHORS.items():
            _anchor_cache[axis] = (embed(low), embed(high))
    return _anchor_cache


def default_embedder() -> Callable[[str], list[float]] | None:
    """Real embedder using the same model/space as the stored Style vectors, or
    None when no API key is configured (radar then degrades to all-null)."""
    global _genai_client
    key = os.environ.get("GOOGLE_SEARCH_API_KEY")
    if not key:
        return None
    if _genai_client is None:
        from google import genai

        from agentic_librarian.llm_retry import genai_http_options

        _genai_client = genai.Client(api_key=key, http_options=genai_http_options())
    client = _genai_client
    return lambda text: get_cached_embedding(client, _EMBED_MODEL, text)


def aggregate_radar(
    style_maps: list[dict[str, _StyleLike]],
    embed: Callable[[str], list[float]] | None,
) -> dict[str, float | None]:
    """Mean 0-1 score per axis across the user's read works. None when an axis has
    no scorable data (or no embedder)."""
    scores: dict[str, list[float]] = {axis: [] for axis in AXES}
    if embed is not None:
        anchors = get_anchor_vectors(embed)
        for style_map in style_maps:
            for attr, style in style_map.items():
                axis = RADAR_ATTR_TO_AXIS.get(attr)
                if axis is None or style.embedding is None:
                    continue
                low, high = anchors[axis]
                s = score_axis(style.embedding, low, high)
                if s is not None:
                    scores[axis].append(s)
    return {axis: (math.fsum(v) / len(v) if v else None) for axis, v in scores.items()}


def aggregate_cloud(style_maps: list[dict[str, _StyleLike]], top_n: int = 20) -> list[dict]:
    """Frequency of nominal style values across read works, title-cased so
    case/punctuation duplicates merge."""
    counter: Counter[str] = Counter()
    for style_map in style_maps:
        for attr, style in style_map.items():
            if attr in CLOUD_ATTRS and style.name:
                counter[style.name.title()] += 1
    return [{"name": name, "count": count} for name, count in counter.most_common(top_n)]
```

Run: `python -m pytest test/unit/test_analysis_style.py -q`
Expected: PASS (all tests). Note: `aggregate_radar` tests mutate `_anchor_cache`; add `m._anchor_cache.clear()` in a pytest fixture if cross-test bleed appears — but with the deterministic fake embedder the cached anchors are identical, so it's safe.

- [ ] **Step 6: Lint, format, commit**

```bash
uvx ruff@0.15.16 check --fix src/agentic_librarian/api/analysis_style.py test/unit/test_analysis_style.py
uvx ruff@0.15.16 format src/agentic_librarian/api/analysis_style.py test/unit/test_analysis_style.py
git add src/agentic_librarian/api/analysis_style.py test/unit/test_analysis_style.py
git commit -m "feat(analysis): style-scoring module (anchor-projection radar + nominal cloud)"
```

---

### Task 3: Wire style scoring into the `/analysis` endpoint

Load the style relationships, build one style map per read work (author baseline overlaid with per-work overrides), and add `style_radar` + `style_cloud` to the response.

**Files:**
- Modify: `src/agentic_librarian/api/analysis.py`
- Test: `test/integration/test_analysis_api.py`

- [ ] **Step 1: Write failing db_integration tests**

Extend `test/integration/test_analysis_api.py`. First widen `_seed_read` to optionally attach styles — add a `styles=None` kwarg (`styles` is `dict[attribute_type, (style_name, category)]`) and seed them:

```python
from agentic_librarian.db.models import (
    Author, AuthorStyle, Edition, Narrator, ReadingHistory, Style,
    Trope, User, Work, WorkContributor, WorkStyle, WorkTrope,
)

# ... inside _seed_read signature add:  styles=None,
# ... after creating author `a` and before/after the edition, add:
        if styles:
            for attr, (sname, category) in styles.items():
                st = Style(name=sname, category=category)
                s.add(st)
                s.flush()
                if category == "Author":
                    s.add(AuthorStyle(author_id=a.id, style_id=st.id, attribute_type=attr))
                else:  # "Work"
                    s.add(WorkStyle(work_id=work.id, style_id=st.id, attribute_type=attr))
```

Then add two tests:

```python
def test_analysis_includes_style_radar_and_cloud_keys(client, db_url):
    manager = DatabaseManager(db_url)
    _seed_read(
        manager, user_id=DEFAULT_USER_ID, title="Dune", author="Herbert",
        genres=["Sci-Fi"], moods=["epic"], tropes=["chosen-one"],
        styles={
            "pacing": ("measured", "Author"),
            "tone": ("atmospheric", "Author"),
            "perspective": ("third person omniscient", "Work"),
        },
    )
    body = client.get("/analysis").json()

    # radar always present with all eight axis keys; values may be null without
    # embeddings / API key (the degrade path) -- shape is the contract.
    assert set(body["style_radar"].keys()) == {
        "pace", "density", "depth", "inner_focus",
        "humor", "warmth", "lexicon", "world_building",
    }
    # cloud counts the nominal styles, title-cased; radar attrs (pacing) excluded.
    cloud = {row["name"]: row["count"] for row in body["style_cloud"]}
    assert cloud == {"Atmospheric": 1, "Third Person Omniscient": 1}


def test_analysis_empty_user_has_empty_style_fields(client):
    body = client.get("/analysis").json()
    assert body["style_cloud"] == []
    assert all(v is None for v in body["style_radar"].values())
```

Run: `python -m pytest test/integration/test_analysis_api.py -q -m db_integration`
Expected: FAIL — response has no `style_radar`/`style_cloud` keys (and `Style`/`AuthorStyle`/`WorkStyle` import works since they already exist in models).

- [ ] **Step 2: Load style relationships in the query**

In `src/agentic_librarian/api/analysis.py`, update the imports and the `joinedload` options. Add to the model import (line ~17):

```python
from agentic_librarian.db.models import (
    AuthorStyle, Edition, ReadingHistory, Work, WorkContributor, WorkStyle, WorkTrope,
)
```

Add two loader chains inside `.options(...)` (after the existing narrators loader, ~line 54):

```python
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .selectinload(Work.contributors)
                .joinedload(WorkContributor.author)
                .selectinload(Author.styles)
                .joinedload(AuthorStyle.style),
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .selectinload(Work.styles)
                .joinedload(WorkStyle.style),
```

(Import `Author` too if not already imported — it is needed for `Author.styles`.)

- [ ] **Step 3: Build style maps and call the aggregators**

In `analysis.py`, import the scoring module near the top:

```python
from agentic_librarian.api import analysis_style
```

Inside `get_analysis`, build one style map per read row while iterating `rows` (extend the existing `for r in rows:` loop, or add a second pass). Add a `style_maps: list[dict] = []` alongside the other counters, and inside the loop:

```python
            baseline: dict = {}
            for c in work.contributors:
                if c.role == "Author":
                    for asty in c.author.styles:
                        baseline[asty.attribute_type] = asty.style
            overrides = {wsty.attribute_type: wsty.style for wsty in work.styles}
            style_maps.append({**baseline, **overrides})
```

Then before `return`, compute:

```python
        embed = analysis_style.default_embedder()
        style_radar = analysis_style.aggregate_radar(style_maps, embed)
        style_cloud = analysis_style.aggregate_cloud(style_maps)
```

And add the two fields to the returned dict:

```python
            "narrators": _ranked(narrators),
            "style_radar": style_radar,
            "style_cloud": style_cloud,
```

- [ ] **Step 4: Run the integration tests**

Run: `python -m pytest test/integration/test_analysis_api.py -q -m db_integration`
Expected: PASS — including the pre-existing tests (the two new fields are additive). The radar values will be `null` in CI if no `GOOGLE_SEARCH_API_KEY`, which the tests allow.

- [ ] **Step 5: Lint, format, commit**

```bash
uvx ruff@0.15.16 check --fix src/agentic_librarian/api/analysis.py test/integration/test_analysis_api.py
uvx ruff@0.15.16 format src/agentic_librarian/api/analysis.py test/integration/test_analysis_api.py
git add src/agentic_librarian/api/analysis.py test/integration/test_analysis_api.py
git commit -m "feat(analysis): return style_radar + style_cloud from /analysis"
```

---

### Task 4: `WordCloud` component (trope + style clouds)

**Files:**
- Create: `frontend/src/components/WordCloud.tsx`, `frontend/src/components/WordCloud.css`
- Test: `frontend/src/components/WordCloud.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/WordCloud.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import WordCloud from './WordCloud'

const items = [
  { name: 'Found Family', count: 30 },
  { name: 'Slow Burn', count: 18 },
  { name: 'Chosen One', count: 3 },
]

describe('WordCloud', () => {
  it('renders every entry as readable text', () => {
    render(<WordCloud items={items} />)
    for (const it of items) expect(screen.getByText(it.name)).toBeInTheDocument()
  })

  it('scales the most frequent larger than the least frequent', () => {
    render(<WordCloud items={items} />)
    const big = screen.getByText('Found Family')
    const small = screen.getByText('Chosen One')
    expect(parseFloat(big.style.fontSize)).toBeGreaterThan(parseFloat(small.style.fontSize))
  })

  it('assigns a non-empty color to the smallest word (never faded out)', () => {
    render(<WordCloud items={items} />)
    expect(screen.getByText('Chosen One').style.color).toMatch(/var\(--cat-/)
  })

  it('renders nothing when empty', () => {
    const { container } = render(<WordCloud items={[]} />)
    expect(container.firstChild).toBeNull()
  })
})
```

Run: `cd frontend; npm test -- WordCloud`
Expected: FAIL (no component).

- [ ] **Step 2: Implement the component**

Create `frontend/src/components/WordCloud.tsx`:

```tsx
import type { Ranked } from '../api/client'
import './WordCloud.css'

const MIN_PX = 13
const MAX_PX = 30

/** A frequency-sized word cloud. Size + weight encode count; color cycles the
 * categorical palette so every word — including the smallest — stays readable.
 * Shared by the trope cloud and the style cloud. */
export default function WordCloud({ items }: { items: Ranked[] }) {
  if (items.length === 0) return null
  const counts = items.map((i) => i.count)
  const lo = Math.min(...counts)
  const hi = Math.max(...counts)
  const size = (c: number) => (hi === lo ? (MIN_PX + MAX_PX) / 2 : MIN_PX + ((c - lo) / (hi - lo)) * (MAX_PX - MIN_PX))

  return (
    <ul className="word-cloud">
      {items.map((it, idx) => {
        const px = size(it.count)
        return (
          <li key={it.name}>
            <span
              style={{
                fontSize: `${px.toFixed(1)}px`,
                color: `var(--cat-${(idx % 6) + 1})`,
                fontWeight: px > 22 ? 600 : 400,
              }}
            >
              {it.name}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
```

Create `frontend/src/components/WordCloud.css`:

```css
.word-cloud {
  list-style: none; margin: 0; padding: 0;
  display: flex; flex-wrap: wrap; align-items: baseline;
  gap: var(--space-2) var(--space-4); justify-content: center;
  line-height: 1.9;
}
.word-cloud li { display: inline; }
```

- [ ] **Step 3: Run the tests**

Run: `cd frontend; npm test -- WordCloud`
Expected: PASS (4 tests).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/WordCloud.tsx frontend/src/components/WordCloud.css frontend/src/components/WordCloud.test.tsx
git commit -m "feat(analysis): WordCloud component (frequency-sized, categorical color)"
```

---

### Task 5: `ProportionBar` component (format mix)

**Files:**
- Create: `frontend/src/components/ProportionBar.tsx`, `frontend/src/components/ProportionBar.css`
- Test: `frontend/src/components/ProportionBar.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/ProportionBar.test.tsx`:

```tsx
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ProportionBar from './ProportionBar'

const items = [
  { name: 'Audiobook', count: 58 },
  { name: 'Ebook', count: 27 },
  { name: 'Hardcover', count: 11 },
  { name: 'Paperback', count: 4 },
]

describe('ProportionBar', () => {
  it('renders a legend entry per format with a percentage', () => {
    render(<ProportionBar items={items} />)
    const legend = screen.getByRole('list', { name: /format legend/i })
    expect(within(legend).getByText(/Audiobook/)).toBeInTheDocument()
    expect(within(legend).getByText(/58%/)).toBeInTheDocument()
    expect(within(legend).getByText(/Paperback/)).toBeInTheDocument()
  })

  it('orders segments largest to smallest', () => {
    render(<ProportionBar items={[items[2], items[0], items[1], items[3]]} />)
    const segs = screen.getAllByTestId('segment')
    const widths = segs.map((s) => parseFloat(s.style.width))
    expect(widths).toEqual([...widths].sort((a, b) => b - a))
  })

  it('renders nothing when empty', () => {
    const { container } = render(<ProportionBar items={[]} />)
    expect(container.firstChild).toBeNull()
  })
})
```

Run: `cd frontend; npm test -- ProportionBar`
Expected: FAIL (no component).

- [ ] **Step 2: Implement the component**

Create `frontend/src/components/ProportionBar.tsx`:

```tsx
import type { Ranked } from '../api/client'
import './ProportionBar.css'

const INLINE_LABEL_MIN_PCT = 12 // show the label inside the segment above this width

/** A single horizontal 100%-stacked proportion bar: segments ordered largest ->
 * smallest, categorical colors, legend below as the source of truth. */
export default function ProportionBar({ items }: { items: Ranked[] }) {
  if (items.length === 0) return null
  const total = items.reduce((sum, it) => sum + it.count, 0) || 1
  const sorted = [...items].sort((a, b) => b.count - a.count)
  const pct = (c: number) => (c / total) * 100

  return (
    <div className="proportion">
      <div className="proportion-bar" role="img" aria-label={
        'Format mix: ' + sorted.map((it) => `${it.name} ${Math.round(pct(it.count))}%`).join(', ')
      }>
        {sorted.map((it, idx) => {
          const p = pct(it.count)
          return (
            <div
              key={it.name}
              data-testid="segment"
              className="proportion-seg"
              style={{ width: `${p}%`, background: `var(--cat-${(idx % 6) + 1})` }}
            >
              {p >= INLINE_LABEL_MIN_PCT && <span>{it.name} {Math.round(p)}%</span>}
            </div>
          )
        })}
      </div>
      <ul className="proportion-legend" aria-label="format legend">
        {sorted.map((it, idx) => (
          <li key={it.name}>
            <span className="swatch" style={{ background: `var(--cat-${(idx % 6) + 1})` }} aria-hidden="true" />
            {it.name} · {Math.round(pct(it.count))}%
          </li>
        ))}
      </ul>
    </div>
  )
}
```

Create `frontend/src/components/ProportionBar.css`:

```css
.proportion-bar {
  display: flex; width: 100%; height: 34px;
  border-radius: var(--radius); overflow: hidden;
  border: 1px solid var(--border);
}
.proportion-seg {
  min-width: 6px; display: flex; align-items: center; justify-content: center;
  color: #1c1812; font: 600 var(--fs-xs) var(--font-display);
  white-space: nowrap; overflow: hidden;
}
.proportion-legend {
  list-style: none; margin: var(--space-3) 0 0; padding: 0;
  display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-4);
  font-size: var(--fs-sm); color: var(--text-muted);
}
.proportion-legend li { display: flex; align-items: center; gap: var(--space-2); }
.proportion-legend .swatch { width: 11px; height: 11px; border-radius: 2px; display: inline-block; }
```

- [ ] **Step 3: Run the tests**

Run: `cd frontend; npm test -- ProportionBar`
Expected: PASS (3 tests).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ProportionBar.tsx frontend/src/components/ProportionBar.css frontend/src/components/ProportionBar.test.tsx
git commit -m "feat(analysis): ProportionBar component (stacked format mix + legend)"
```

---

### Task 6: `StyleRadar` component

Recharts `RadarChart`. Because Recharts renders nothing measurable under jsdom, the component also exposes an accessible summary (`role="img"` + `aria-label`) that the tests assert on; visual correctness is verified later via the QC harness. Degrades when fewer than 3 axes have data.

**Files:**
- Create: `frontend/src/components/StyleRadar.tsx`, `frontend/src/components/StyleRadar.css`
- Test: `frontend/src/components/StyleRadar.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/StyleRadar.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { StyleRadar as Radar } from '../api/client'
import StyleRadar from './StyleRadar'

const full: Radar = {
  pace: 0.7, density: 0.4, depth: 0.6, inner_focus: 0.5,
  humor: 0.2, warmth: 0.7, lexicon: 0.5, world_building: 0.8,
}

describe('StyleRadar', () => {
  it('renders an accessible summary naming the axes when data is present', () => {
    render(<StyleRadar radar={full} />)
    const fig = screen.getByRole('img', { name: /shape of your reading/i })
    expect(fig).toBeInTheDocument()
    expect(fig.getAttribute('aria-label')).toMatch(/pace/i)
  })

  it('shows the gathering message when radar is undefined', () => {
    render(<StyleRadar radar={undefined} />)
    expect(screen.getByText(/gathering your style/i)).toBeInTheDocument()
  })

  it('shows the gathering message when fewer than three axes have data', () => {
    render(<StyleRadar radar={{ ...emptyRadar, pace: 0.5, humor: 0.3 }} />)
    expect(screen.getByText(/gathering your style/i)).toBeInTheDocument()
  })
})

const emptyRadar: Radar = {
  pace: null, density: null, depth: null, inner_focus: null,
  humor: null, warmth: null, lexicon: null, world_building: null,
}
```

Run: `cd frontend; npm test -- StyleRadar`
Expected: FAIL (no component).

- [ ] **Step 2: Implement the component**

Create `frontend/src/components/StyleRadar.tsx`:

```tsx
import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer } from 'recharts'
import type { StyleAxis, StyleRadar as RadarData } from '../api/client'
import './StyleRadar.css'

const AXIS_LABEL: Record<StyleAxis, string> = {
  pace: 'Pace', density: 'Density', depth: 'Depth', inner_focus: 'Inner focus',
  humor: 'Humor', warmth: 'Warmth', lexicon: 'Lexicon', world_building: 'World-building',
}
const ORDER: StyleAxis[] = ['pace', 'density', 'depth', 'inner_focus', 'humor', 'warmth', 'lexicon', 'world_building']
const MIN_AXES = 3

export default function StyleRadar({ radar }: { radar?: RadarData }) {
  const points = radar
    ? ORDER.filter((a) => radar[a] !== null).map((a) => ({ axis: AXIS_LABEL[a], value: radar[a] as number }))
    : []

  if (points.length < MIN_AXES) {
    return <p className="radar-empty muted">Gathering your style… read a few more books and your shape will appear.</p>
  }

  const summary = 'The shape of your reading: ' + points.map((p) => `${p.axis} ${Math.round(p.value * 100)}%`).join(', ')

  return (
    <div className="style-radar" role="img" aria-label={summary}>
      <ResponsiveContainer width="100%" height={280}>
        <RadarChart data={points} outerRadius="72%">
          <PolarGrid className="radar-grid" />
          <PolarAngleAxis dataKey="axis" className="radar-axis" tick={{ fontSize: 12 }} />
          <PolarRadiusAxis domain={[0, 1]} tick={false} axisLine={false} />
          <Radar className="radar-shape" dataKey="value" fillOpacity={0.3} isAnimationActive={false} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}
```

Create `frontend/src/components/StyleRadar.css` (theme-reactive fills via CSS vars, since Recharts attributes can't resolve `var()` directly):

```css
.style-radar { width: 100%; }
.radar-empty { text-align: center; padding: var(--space-6) 0; }
.style-radar .radar-shape { fill: var(--gilt); stroke: var(--gilt); }
.style-radar .radar-grid line,
.style-radar .radar-grid polygon { stroke: var(--border); }
.style-radar .radar-axis text { fill: var(--text-muted); font-family: var(--font-body); }
```

- [ ] **Step 3: Run the tests**

Run: `cd frontend; npm test -- StyleRadar`
Expected: PASS (3 tests). If Recharts emits a width/height warning under jsdom, it's harmless — the assertions target the `role="img"` summary and the degrade text, not the SVG.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/StyleRadar.tsx frontend/src/components/StyleRadar.css frontend/src/components/StyleRadar.test.tsx
git commit -m "feat(analysis): StyleRadar component (recharts radar + degrade + a11y summary)"
```

---

### Task 7: `GenreMoodBars` component (genre & mood bar charts)

Recharts `BarChart`, single-hue gilt. Same a11y-summary approach as the radar.

**Files:**
- Create: `frontend/src/components/GenreMoodBars.tsx`, `frontend/src/components/GenreMoodBars.css`
- Test: `frontend/src/components/GenreMoodBars.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/GenreMoodBars.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import GenreMoodBars from './GenreMoodBars'

const items = [
  { name: 'Fantasy', count: 58 },
  { name: 'Science Fiction', count: 47 },
]

describe('GenreMoodBars', () => {
  it('renders an accessible summary with the title and entries', () => {
    render(<GenreMoodBars title="Genres" items={items} />)
    const fig = screen.getByRole('img', { name: /genres/i })
    expect(fig.getAttribute('aria-label')).toMatch(/Fantasy 58/)
  })

  it('shows an empty message when there is no data', () => {
    render(<GenreMoodBars title="Moods" items={[]} />)
    expect(screen.getByText(/no data yet/i)).toBeInTheDocument()
  })
})
```

Run: `cd frontend; npm test -- GenreMoodBars`
Expected: FAIL (no component).

- [ ] **Step 2: Implement the component**

Create `frontend/src/components/GenreMoodBars.tsx`:

```tsx
import { Bar, BarChart, ResponsiveContainer, XAxis, YAxis } from 'recharts'
import type { Ranked } from '../api/client'
import './GenreMoodBars.css'

/** Horizontal bar chart for genres or moods. Single-hue gilt (row labels
 * differentiate). Exposes an accessible summary for tests + screen readers. */
export default function GenreMoodBars({ title, items }: { title: string; items: Ranked[] }) {
  if (items.length === 0) return <p className="muted">No data yet.</p>

  const summary = `${title}: ` + items.map((it) => `${it.name} ${it.count}`).join(', ')
  const height = Math.max(120, items.length * 34)

  return (
    <div className="genre-mood-bars" role="img" aria-label={summary}>
      <h3>{title}</h3>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={items} layout="vertical" margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
          <XAxis type="number" hide />
          <YAxis type="category" dataKey="name" width={120} className="bars-axis" tick={{ fontSize: 12 }} />
          <Bar className="bars-bar" dataKey="count" radius={[0, 4, 4, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
```

Create `frontend/src/components/GenreMoodBars.css`:

```css
.genre-mood-bars { width: 100%; }
.genre-mood-bars h3 { margin: 0 0 var(--space-2); }
.genre-mood-bars .bars-bar { fill: var(--gilt); }
.genre-mood-bars .bars-axis text { fill: var(--text-muted); font-family: var(--font-body); }
```

- [ ] **Step 3: Run the tests**

Run: `cd frontend; npm test -- GenreMoodBars`
Expected: PASS (2 tests).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/GenreMoodBars.tsx frontend/src/components/GenreMoodBars.css frontend/src/components/GenreMoodBars.test.tsx
git commit -m "feat(analysis): GenreMoodBars component (recharts horizontal bars)"
```

---

### Task 8: Rewrite `AnalysisView` as the single-scroll dashboard

Compose all sections into one scroll, retiring the tabs. Rewrite the view test and refresh the QC fixture.

**Files:**
- Modify (rewrite): `frontend/src/views/AnalysisView.tsx`, `frontend/src/views/AnalysisView.css`
- Modify (rewrite): `frontend/src/views/AnalysisView.test.tsx`
- Modify: `frontend/qc.tsx:57-64` (extend the `analysis` fixture)

- [ ] **Step 1: Rewrite the view test**

Replace `frontend/src/views/AnalysisView.test.tsx` entirely:

```tsx
import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Analysis } from '../api/client'

vi.mock('../api/client', () => ({ getAnalysis: vi.fn() }))

import { getAnalysis } from '../api/client'
import AnalysisView from './AnalysisView'

const base: Analysis = {
  snapshot: {
    total_read: 12, read_this_year: 4, average_rating: 4.2, distinct_authors: 9,
    formats: [{ name: 'Audiobook', count: 10 }, { name: 'Ebook', count: 2 }],
  },
  genres: [{ name: 'Sci-Fi', count: 6 }],
  moods: [{ name: 'Epic', count: 5 }],
  top_tropes: [{ name: 'Chosen One', count: 3 }],
  authors: [{ name: 'Herbert', count: 2 }],
  narrators: [{ name: 'Vance', count: 4 }],
  style_radar: {
    pace: 0.7, density: 0.4, depth: 0.6, inner_focus: 0.5,
    humor: 0.2, warmth: 0.7, lexicon: 0.5, world_building: 0.8,
  },
  style_cloud: [{ name: 'Atmospheric', count: 7 }, { name: 'Lyrical', count: 3 }],
}

describe('AnalysisView', () => {
  beforeEach(() => vi.mocked(getAnalysis).mockResolvedValue(base))
  afterEach(() => vi.clearAllMocks())

  it('renders the snapshot, tropes, style, and people in one scroll', async () => {
    render(<AnalysisView />)
    expect(await screen.findByText('12')).toBeInTheDocument()        // snapshot
    expect(screen.getByText('Chosen One')).toBeInTheDocument()       // trope cloud
    expect(screen.getByText('Atmospheric')).toBeInTheDocument()      // style cloud
    expect(screen.getByText('Vance')).toBeInTheDocument()            // people
    expect(screen.getByRole('img', { name: /shape of your reading/i })).toBeInTheDocument()
  })

  it('degrades gracefully when style fields are absent', async () => {
    vi.mocked(getAnalysis).mockResolvedValueOnce({ ...base, style_radar: undefined, style_cloud: undefined })
    render(<AnalysisView />)
    expect(await screen.findByText('12')).toBeInTheDocument()
    expect(screen.getByText(/gathering your style/i)).toBeInTheDocument()
  })
})
```

Run: `cd frontend; npm test -- AnalysisView`
Expected: FAIL — the current tabbed view has no such structure.

- [ ] **Step 2: Rewrite the view**

Replace `frontend/src/views/AnalysisView.tsx` entirely:

```tsx
import { useEffect, useState } from 'react'
import GenreMoodBars from '../components/GenreMoodBars'
import ProportionBar from '../components/ProportionBar'
import StyleRadar from '../components/StyleRadar'
import WordCloud from '../components/WordCloud'
import { getAnalysis, type Analysis, type Ranked } from '../api/client'
import './AnalysisView.css'

function RankedList({ title, items }: { title: string; items: Ranked[] }) {
  return (
    <div className="ranked">
      <h3>{title}</h3>
      {items.length === 0 ? (
        <p className="muted">No data yet.</p>
      ) : (
        <ul>
          {items.map((it) => (
            <li key={it.name}>
              <span>{it.name}</span>
              <span className="count">{it.count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="analysis-section">
      <h2 className="section-title">{title}</h2>
      {children}
    </section>
  )
}

export default function AnalysisView() {
  const [data, setData] = useState<Analysis | null>(null)

  useEffect(() => {
    void getAnalysis().then(setData)
  }, [])

  if (data === null) return <p>Loading…</p>

  return (
    <div className="analysis">
      <h2>Analysis</h2>

      <Section title="Your reading">
        <div className="snapshot-grid">
          <div className="stat"><span className="stat-num">{data.snapshot.total_read}</span><span>books read</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.read_this_year}</span><span>this year</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.average_rating ?? '—'}</span><span>avg rating</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.distinct_authors}</span><span>authors</span></div>
        </div>
        <ProportionBar items={data.snapshot.formats} />
      </Section>

      <Section title="The shape of your reading">
        <StyleRadar radar={data.style_radar} />
      </Section>

      <Section title="Your signature tropes">
        <WordCloud items={data.top_tropes} />
      </Section>

      <Section title="Genre & mood">
        <div className="two-col">
          <GenreMoodBars title="Genres" items={data.genres} />
          <GenreMoodBars title="Moods" items={data.moods} />
        </div>
      </Section>

      {data.style_cloud && data.style_cloud.length > 0 && (
        <Section title="Your style">
          <WordCloud items={data.style_cloud} />
        </Section>
      )}

      <Section title="Authors & narrators">
        <div className="two-col">
          <RankedList title="Authors" items={data.authors} />
          <RankedList title="Narrators" items={data.narrators} />
        </div>
      </Section>
    </div>
  )
}
```

- [ ] **Step 3: Update the view styles**

Replace `frontend/src/views/AnalysisView.css` — keep the existing `.snapshot-grid`, `.stat`, `.stat-num`, `.two-col`, `.ranked`, `.count`, `.muted` rules (read the current file and preserve them), and add section spacing:

```css
.analysis-section { margin-top: var(--space-7); }
.analysis-section .section-title { font-size: var(--fs-title); margin: 0 0 var(--space-4); }
```

Remove the now-unused `.tabs` / `.tab` rules. Keep everything else.

- [ ] **Step 4: Run the view tests**

Run: `cd frontend; npm test -- AnalysisView`
Expected: PASS (2 tests).

- [ ] **Step 5: Refresh the QC fixture**

In `frontend/qc.tsx`, extend the `analysis` fixture object (line ~57) with realistic style data so the harness renders the radar + style cloud:

```ts
  style_radar: { pace: 0.74, density: 0.42, depth: 0.66, inner_focus: 0.55, humor: 0.21, warmth: 0.7, lexicon: 0.5, world_building: 0.82 },
  style_cloud: [
    { name: 'Atmospheric', count: 22 }, { name: 'Lyrical', count: 14 }, { name: 'Cynical', count: 9 },
    { name: 'Minimalist', count: 7 }, { name: 'First Person', count: 12 }, { name: 'Unreliable', count: 5 },
    { name: 'Wry', count: 4 }, { name: 'Naturalistic', count: 3 },
  ],
```

- [ ] **Step 6: Full frontend gate + commit**

Run: `cd frontend; npm run lint; npm test`
Expected: lint clean; entire suite passes.

```bash
git add frontend/src/views/AnalysisView.tsx frontend/src/views/AnalysisView.css frontend/src/views/AnalysisView.test.tsx frontend/qc.tsx
git commit -m "feat(analysis): single-scroll dashboard composing radar, clouds, bars, proportion bar"
```

- [ ] **Step 7: Visual QC (headless harness, both themes)**

Per memory `qc-harness` / `docs/frontend-visual-qc.md`: run the dev server, point the harness at `/analysis`, screenshot light + dark, and read the PNGs back. Confirm: radar shape + axis labels legible; trope/style clouds readable down to the smallest word; proportion bar segments + legend correct; bars labeled; spacing of the scroll. Fix any visual issues found (CSS only) and amend/commit. This is verification, not a code path — no test asserts pixels.

---

## Self-Review

**1. Spec coverage:**
- §3 single-scroll composition → Task 8 (all sections in order). ✓
- §5 radar (subject A, 8 axes, embedding-projection, degrade) → Tasks 2 (scoring) + 6 (component, <3-axis degrade). ✓
- §6 clouds (shared component, title-case, frequency-by-size) → Task 4 (component) + Task 2 `aggregate_cloud` (title-case). ✓
- §7 / §7.1 charts + categorical palette + proportion bar → Tasks 1 (tokens), 5 (proportion bar), 6 (radar), 7 (bars). ✓
- §8 API contract (`style_radar`, `style_cloud`, optional, graceful degrade) → Tasks 1 (types), 3 (endpoint), 8 (degrade test). ✓
- §9 file structure → matches Tasks 1–8. ✓
- §10 edge cases (empty shelf, partial data, backend absent, anchor fetch fail, dark mode) → Task 3 (empty/null radar), Task 6 (degrade), Task 2 (`default_embedder` None path), Task 8 step 7 (themes). ✓
- §11 testing → unit (Task 2), db_integration (Task 3), component + view (Tasks 4–8), QC harness (Task 8 step 7). ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. "Read the current file and preserve them" (Task 8 step 3) refers to concrete existing CSS rules, not a placeholder.

**3. Type consistency:** `StyleAxis` / `StyleRadar` (Task 1) reused verbatim in Tasks 6 & 8. `Ranked` reused in Tasks 4/5/7. Backend `AXES` / `RADAR_ATTR_TO_AXIS` / `ANCHORS` keys (Task 2) match the eight `style_radar` JSON keys asserted in Task 3 and the frontend `StyleAxis` union (Task 1). `score_axis` / `aggregate_radar` / `aggregate_cloud` / `default_embedder` signatures defined in Task 2 are called with matching arguments in Task 3. Component prop names (`items`, `radar`, `title`) consistent between each component and its consumer in Task 8.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-06-25-analysis-viz-upgrade.md`.
