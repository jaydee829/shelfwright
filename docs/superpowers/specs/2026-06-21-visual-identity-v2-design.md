# Visual Identity v2 — "Arcane Library" — Design

**Date:** 2026-06-21
**Status:** Approved (brainstormed), pending plan
**Worktree / branch:** `.claude/worktrees/design-work` / `worktree-design-work` (off `main`)
**Scope:** Frontend visual layer only — no backend, no data/business logic.
**Origin:** Product roadmap NEXT item ("Visual Identity v2"). The app shipped a functional
light/dark theme in E1 (PR #54) built on a CSS-variable token system, but the palette is generic
("AI indigo" `#6366f1`, `system-ui` type, neutral grays). This gives the product a distinctive,
cohesive identity that suits a personal-library book recommender.

---

## 1. Background & the design we settled on

**Code reality this builds on:**

- **Theming already works via CSS variables.** `frontend/src/index.css` defines a `:root` token
  block and a `:root[data-theme="dark"]` override block. `frontend/src/theme.ts` toggles
  `document.documentElement.dataset.theme` and persists the choice (respects
  `prefers-color-scheme`). **This mechanism is kept as-is.**
- **Components consume tokens, not hex.** `TopBar`/`AppShell` use `--topbar-*`, `Nav` uses
  `--nav-fg`/`--surface`/`--surface-2`, `RecommendationsView` uses `--surface-2`/`--badge-*`,
  chips use `--chip-*`, etc. So **most of the identity ships by re-defining the token blocks**;
  per-component CSS is only needed for the new *motifs* (book-spine, page fore-edge, leaf-cut
  chips, brand wordmark, nav active indicator) and for loading fonts.
- **No web fonts today.** `index.html` is bare; body font is `system-ui`. Introducing display +
  body faces is a genuine (small) new dependency — see §7.

**The identity — "Arcane Library":** bookish, crafted warmth (candlelit reading room) with
restrained *ethereal* pops (enchanted ink / witchlight). Two atmospheres from one palette: a warm
**parchment** light theme ("Candlelit") and a deep **ink-violet** dark theme ("Midnight
Athenaeum"). Validated interactively against a real recommendation card in both themes.

### The signature idea: a four-hue *role rotation* across themes

Each accent has a home in **both** light and dark, but trades roles between them. This is what makes
the two themes feel like one identity rather than two skins:

| Hue | Light ("Candlelit") role | Dark ("Midnight Athenaeum") role |
|---|---|---|
| **Violet** `#6d4ed6` | Special **glow** (highlighted/"why this" trope) | Secondary **trope** chips |
| **Teal** `#1f9e94` | Book **spine** accent (card left edge) | Special **glow** (highlighted trope) |
| **Oxblood** `#9a3b2e` | Primary **button** | Book **spine** accent (card left edge) |
| **Gold / gilt** `#c79a3e` | Constant — gilding, stars, flourishes | Constant — gilding, stars, flourishes (brighter `#e3b85e`) |

Teal & oxblood swap which theme wears them as a *spine*; violet & teal swap which theme wears them
as a *glow*; gold gilds both. (The "special glow" hue is therefore violet by day, teal by night —
intentional.)

---

## 2. Goals

- Replace the generic palette/type with the **Arcane Library** identity across the whole SPA,
  light and dark, primarily by re-defining the existing CSS-variable token blocks.
- Establish a **design-token system**: color (mapped onto existing var names + new ones),
  typography (display + body faces, type scale), spacing, radius, shadow/elevation, and the
  motif tokens (spine, page-edge, glow, gilt).
- Define a **form language**: the "book" card (rounded bound spine left, crisp page fore-edge
  right), leaf-cut chips, sparing ✦ flourish, gilt hairlines.
- Apply the identity to **shared chrome** (top bar wordmark, nav active state, app shell/parchment
  ground) and **every view's CSS**, so the product is cohesive end to end.
- Stay **accessible** (WCAG AA contrast, visible focus, `prefers-reduced-motion`,
  glow-is-never-the-only-signal) and **performant** (self-hosted subset fonts, CSS-only effects).

## 3. Non-Goals (YAGNI)

- **No new views, routes, or features.** Purely visual.
- **No backend / API / data changes.** Nothing under `src/agentic_librarian/**`.
- **No edits to files held by the bulk-import branch** — `App.tsx`, `views/HistoryView.tsx`,
  `api/client.ts` (see §9). History is restyled via `HistoryView.css` only; if a structural
  `.tsx` change is unavoidable it is coordinated on the board first.
- **No animation framework / motion system.** Glows are static CSS; only the existing 0.15s
  theme transition remains.
- **No commissioned logo or illustration.** The brand mark is a typeset wordmark + ✦ glyph.
- **No paper-grain raster textures** in v1 (kept as a CSS-gradient option; see open questions).
- **No responsive-layout overhaul** beyond what the restyle naturally touches.

---

## 4. Design tokens

All tokens live in `frontend/src/index.css`. Existing variable **names are preserved** (so no
component churn); their **values change**, and new tokens are added. Values below are the
approved hexes from the brainstorm.

### 4.1 Color — existing vars, re-valued

| Token | Light value | Dark value | Notes |
|---|---|---|---|
| `--bg` | `#f3e8d2` | `#14121d` | Parchment page / ink-violet night |
| `--surface` | `#f9efd9` | `#1d1a28` | Raised parchment / card; dark card may use gradient (see 4.4) |
| `--surface-2` | `#ecdebf` | `#2a2440` | Sunken / chip ground / nav-active ground |
| `--text` | `#241f1a` | `#f4ecda` | Ink / warm parchment-white |
| `--text-soft` | `#2a231b` | `#ece3d2` | Body emphasis |
| `--text-muted` | `#5a4f43` | `#bcb2a3` | Secondary text |
| `--text-faint` | `#8a7d6a` | `#6f6757` | Captions/placeholders |
| `--border` | `#e6d4ad` | `#4a3f5e` | Card hairlines |
| `--accent` | `#9a3b2e` (oxblood) | `#e3b85e` (gilt) | **Primary action**; rotates oxblood→gold per identity |
| `--on-accent` | `#fff3ec` | `#1a160f` | Text on primary |
| `--star` | `#c79a3e` | `#e3b85e` | Gilt stars |
| `--chip-bg` / `--chip-fg` | `#ecdebf` / `#856a3f` | `rgba(124,92,255,.16)` / `#c7b9ff` | Secondary trope chips (tan day → violet night) |
| `--chip-genre-bg` | `#e7d9b8` | `#272e3f` | Genre variant |
| `--badge-new-bg` | `#1f7a6e` (teal) | `#2e8b57` | "New" badge → teal family (ties to spine) |
| `--badge-reread-bg` | `#6d4ed6` (violet) | `#7c6bb0` | "Reread" badge → violet family |
| `--on-badge` | `#ffffff` | `#ffffff` | |
| `--topbar-bg` | `#1f1830` | `#0e0c16` | Constant dark "leather binding" both modes |
| `--topbar-fg` | `#f4ecda` | `#f4ecda` | |
| `--topbar-border` | `#c79a3e` (gilt hairline) | `#b78b3f` | Gilt bottom edge of the binding |
| `--nav-fg` | `#7a6a55` | `#9a8fb0` | Inactive nav |
| `--danger` / `--ok` | `#b23a2b` / `#3f7d4f` | `#f08a7e` / `#79c089` | Warm-tuned semantics |
| `--overlay` / `--menu-shadow` | `rgba(40,25,15,.45)` / `rgba(60,40,20,.18)` | `rgba(0,0,0,.6)` / `rgba(0,0,0,.55)` | |

> The few exact `--text-faint` dark value and any cell marked provisional are finalized during
> implementation against the contrast checks in §8; the intent (warm muted) is fixed.

### 4.2 Color — new tokens

| Token | Light | Dark | Purpose |
|---|---|---|---|
| `--gilt` | `#c79a3e` | `#e3b85e` | Gold gilding, flourishes, hairlines |
| `--spine` | `linear-gradient(180deg,#1f9e94,#14756d)` (teal) | `linear-gradient(180deg,#c64a3a,#8f2f24)` (oxblood) | Card left book-spine |
| `--spine-glow` | `rgba(31,158,148,.45)` | `rgba(198,74,58,.7)` | Spine glow |
| `--glow` | `#6d4ed6` (violet) | `#45e0d0` (teal) | Special-glow hue (eyebrow ✦, special chip) |
| `--glow-soft` | `rgba(109,78,214,.55)` | `rgba(69,224,208,.6)` | Glow shadow |
| `--chip-special-bg` / `--chip-special-fg` | `#ece6ff` / `#5b41b8` | `rgba(69,224,208,.12)` / `#7af0e3` | Highlighted/"why" trope chip |
| `--page-edge` | `repeating-linear-gradient(90deg,#f6eedb 0 2px,#d8c8a0 2px 4px)` | `repeating-linear-gradient(90deg,rgba(227,184,94,.55) 0 1px,transparent 1px 4px)` | Right fore-edge (cream pages day / gilt-edged pages night) |

### 4.3 Typography

- **Display / headings & book titles:** **Literata** (variable, optical sizing). Title weight
  **500**; larger section headers may use 600. Author lines use Literata *italic*. Chosen for
  literary character that stays calm when titles repeat in a list (validated "at scale").
- **Body / UI:** **Inter** (400/500/600) — labels, buttons, descriptions, controls.
- Tokens: `--font-display: 'Literata', Georgia, serif;` `--font-body: 'Inter', system-ui, sans-serif;`
- **Type scale** (rem, body-relative): `--fs-display: 1.6rem` (hero/view titles, Literata 500),
  `--fs-title: 1.3rem` (card titles, Literata 500), `--fs-body: 1rem`, `--fs-sm: 0.875rem`,
  `--fs-xs: 0.6875rem` (eyebrows/labels, uppercase, `letter-spacing:.14em`). Line-heights:
  titles ~1.15, body ~1.5.

### 4.4 Spacing, radius, elevation, motif tokens

- **Spacing scale** (`--space-1..7`): `4, 8, 12, 16, 20, 24, 32` px.
- **Radius:** `--radius-sharp: 3px` (page-edge / fore-edge corners), `--radius: 7px` (buttons,
  inputs), `--radius-lg: 14px` (cards, panels), `--radius-spine: 16px` (bound-edge corner),
  `--radius-pill: 999px`. **Leaf-cut chip:** `--radius-leaf: 4px 12px 4px 12px`.
- **Card book-shape** (the locked atom): `border-radius: var(--radius-spine) var(--radius-sharp)
  var(--radius-sharp) var(--radius-spine)` (rounded bound spine left, crisp page block right),
  `overflow: hidden` so spine/page pseudo-elements clip flush.
  - `::before` = left **spine**, `width:5px`, full height, `background:var(--spine)`,
    `box-shadow:0 0 10px -2px var(--spine-glow)`.
  - `::after` = right **page fore-edge**, `width:14px`, full height, flush,
    `border-left:1px solid var(--border)`, `background:var(--page-edge)` (vertical hairlines).
  - Card right padding clears the page block (`padding-right: ~32px`).
- **Elevation:** `--shadow-card` (light `0 12px 26px -16px rgba(80,50,20,.5)`, dark
  `0 14px 34px -18px rgba(0,0,0,.8)`), plus an inset top sheen on light cards and a `0 0 0 1px
  rgba(gilt,.16)` gilt ring on dark cards. Dark card surface may be
  `linear-gradient(180deg,#211d2e,#1b1826)`.
- **Motifs:** ✦ **sparkle** (eyebrow + special markers, colored `--glow`), ✦ **corner flourish**
  (`--gilt`, sparing, top-right of feature cards), **gilt hairline** rule for dividers
  (`border-top:1px solid var(--border)` + faint gilt shadow). Used **sparingly** — base UI is
  clean; ornament marks special moments only.

---

## 5. Component & view application

Keep each unit's existing structure; restyle via tokens + targeted motif CSS. Files listed are the
ones this work owns (see §9).

- **Recommendation card** (`RecommendationsView.css`, `RecommendationsView.tsx`): the book-form
  atom from §4.4 — spine, page edge, leaf-cut trope chips (`--chip-*`), one **special** chip
  (`--chip-special-*`) for the highlighted trope, gilt stars, oxblood/gilt primary button, ✦
  eyebrow. `--badge-new`/`--badge-reread` restyled to teal/violet families.
- **Top bar** (`AppShell.css`, `TopBar.tsx`): a constant dark **"leather binding"** band
  (`--topbar-bg`) with a **gilt hairline** bottom border (`--topbar-border`). Brand becomes a
  Literata **wordmark** with a leading ✦. Avatar uses `--accent`/`--on-accent`.
- **Nav** (`Nav.css`, `Nav.tsx`): inactive `--nav-fg`; **active item** gains a **spine-style
  indicator** — a gilt/teal accent bar (left rail) / top bar (bottom nav) plus `--surface-2`
  ground and `--text`. Labels Inter.
- **App shell** (`AppShell.css`): parchment/night ground via `--bg`; keep the reading-column
  `max-width:820px`. Optional very-subtle radial warm/teal glows behind content (CSS gradients).
- **Buttons (shared):** primary = `--accent`/`--on-accent` + faint gilt hairline; secondary =
  transparent + `--border` outline, gilt on hover; `--radius`. Add a small shared
  `frontend/src/components/` primitive set (CSS classes and/or a `Button`/`Chip`/`BookCard`
  component) so views and **the bulk-import ImportView** can reuse them (see §9).
- **Other views** (CSS only): `AddBookView.css` (parchment inputs, gilt focus ring, leaf chips),
  `AnalysisView.css`, `ChatView.css` (message bubbles: parchment "your" / inked "librarian",
  Literata for any titles), `ActivityTrail.css`. `HistoryView.css` restyled to the shelf-list
  treatment (Literata titles) **without touching `HistoryView.tsx`**.
- **Focus & states:** global visible focus ring (gilt/teal, `:focus-visible`), hover/disabled
  states consistent with tokens.

---

## 6. Isolation & interfaces

- **`index.css` is the single source of truth** for tokens. Components depend on it only through
  variable names — they don't know the identity. Changing a hue is a one-line token edit.
- **Motif CSS is local** to the component that owns the motif (book card → `RecommendationsView`,
  brand → `TopBar`, nav indicator → `Nav`). Shared primitives (button/chip/book-card) expose
  classes; consumers don't reimplement the spine/page math.
- **`theme.ts` is unchanged** — light/dark stays a `data-theme` swap; v2 only changes what the two
  token blocks contain.

## 7. Font delivery

**Decision: self-host via `@fontsource`** (`@fontsource-variable/literata`,
`@fontsource-variable/inter` or weight subsets) imported in `main.tsx`. Rationale over a Google
Fonts `<link>`: no third-party runtime dependency (works offline / behind strict CSP, matches the
app's privacy posture), no FOIT from a blocked request, deterministic builds. Cost: a few hundred
KB of woff2 added to the bundle — mitigated by shipping only the weights used (Literata 400/500/600
+ italic, Inter 400/500/600) and `font-display: swap`. Add `<link rel="preload">` for the two
primary faces. (Alternative if bundle size is a concern: Google Fonts `<link>` with `preconnect`;
recorded as the fallback.)

## 8. Accessibility & performance

- **Contrast:** every text/background pair must meet **WCAG AA** (≥4.5:1 body, ≥3:1 large). The
  parchment/ink and night pairs are designed for this; muted/faint values are verified during
  implementation and adjusted if short. Glow/spine color is **never the only signal** — the
  special chip also differs in weight/label, badges carry text.
- **Focus:** `:focus-visible` ring on all interactive elements, ≥3:1 against adjacent colors.
- **Motion:** glows are static. `@media (prefers-reduced-motion: reduce)` disables the 0.15s theme
  transition and any hover transitions.
- **Performance:** all effects are CSS (gradients, box-shadow) — no images, no JS. Subset,
  preloaded, `swap` fonts. No layout-thrashing properties animated.

## 9. Scope, ownership & coordination

Per `.git/AGENT_COORDINATION.md` (shared board):

- **This branch OWNS:** `frontend/src/index.css`, `frontend/src/theme.ts` (likely untouched),
  shared chrome (`AppShell.*`, `Nav.*`, `TopBar.tsx`, `SignIn.tsx`, `NotInvited.tsx`), all
  `frontend/src/views/*.css`, `RecommendationsView.tsx`/`AnalysisView.tsx`/`ChatView.tsx`/
  `AddBookView.tsx`/`ActivityTrail.tsx`, new primitives under `frontend/src/components/`, `index.html`
  (font preload), and design docs.
- **AVOID (held by bulk-import PR #55):** `App.tsx`, `views/HistoryView.tsx`, `api/client.ts`,
  all backend. History styling goes through `HistoryView.css` only.
- **Hand-off to bulk-import:** `index.css` tokens + shared `components/` primitives are designed so
  the new `views/ImportView.*` **inherits the identity for free** by consuming tokens/classes
  rather than hardcoding. Coordinate on the board before either side edits the other's files.

## 10. Testing

- **Existing:** `theme.test.ts` (light/dark resolution) and component tests must stay green.
  Components reference unchanged var names, so behavior is unaffected.
- **Add (light):** a token-presence/smoke test asserting the new core tokens resolve in both
  themes; optional automated contrast assertion for the key pairs.
- **Manual visual review:** run the SPA, walk every view in light and dark, confirm the book card,
  brand, nav active state, chips/badges, and focus rings. Capture before/after screenshots.

## 11. Risks & open questions

- **Global `--bg` warming** touches every view at once; intended, but verify no view assumed pure
  white/gray (grep for hardcoded `#fff`/`#f1...` in view CSS during implementation; fold strays
  into tokens).
- **Font bundle size** vs. the self-host decision (§7) — revisit if the woff2 subset is larger than
  expected.
- **Open:** (a) self-host vs. Google `<link>` final call (recommend self-host); (b) subtle paper
  texture — ship the CSS-gradient version or stay flat in v1 (recommend flat in v1); (c) how bold
  the top-bar "binding" should be (constant dark band proposed). These are low-risk and resolved
  early in the plan.

> Out of scope for v1; tracked for later: animated "ink" micro-interactions, a bespoke ✦ logo
> mark, and per-genre accent theming.
