# Visual Identity v2 — "Arcane Library" — Design

**Date:** 2026-06-21 (rev. 2026-06-22 — full visual system validated via the brainstorm visual companion)
**Status:** Approved (brainstormed), pending plan
**Worktree / branch:** `.claude/worktrees/design-work` / `worktree-design-work` (off `main`)
**Scope:** Frontend visual layer (+ a small client-side "what's new" affordance). No backend schema/API changes.
**Origin:** Product roadmap NEXT item ("Visual Identity v2"). The app shipped a functional
light/dark theme in E1 (PR #54) on a CSS-variable token system, but the palette is generic
("AI indigo" `#6366f1`, `system-ui` type, neutral grays). This gives the product a distinctive,
cohesive identity suited to a personal-library book recommender.

---

## 1. The identity we settled on — "Arcane Library"

Bookish, crafted **warmth** (a candlelit reading room) with restrained **ethereal** pops (enchanted
ink / witchlight). Two atmospheres from one palette: a warm **parchment** light theme
("Candlelit") and a deep **ink-violet** dark theme ("Midnight Athenaeum"). Every element below was
validated interactively against real recommendation cards in both themes.

**Pillars (all locked):**

1. **Four-hue role rotation** across themes (the signature idea — see §1.1).
2. **Book-form card** — rounded bound spine (left) + crisp page fore-edge (right) (§5).
3. **Type** — Literata 500 (display/titles) + Inter (body/UI), self-hosted (§4.3, §11).
4. **Atmosphere** — parchment grain (light) + starfield (dark) backgrounds; a leather-grain
   "binding" top bar with a gilt-tooled edge (§6).
5. **Genre iconography** — bare gold line-art genre marks in the card corner, 13-genre roster +
   fallback (§7).
6. **The ✦ "what's new" marker** — sparing, meaningful: flags entries changed since last visit (§8).

### 1.1 The signature idea: four-hue role rotation

Each accent has a home in **both** themes but trades roles between them — this is what makes light
and dark read as one identity rather than two skins.

| Hue | Light ("Candlelit") role | Dark ("Midnight Athenaeum") role |
|---|---|---|
| **Violet** `#6d4ed6` | Special **glow** (highlighted "why" trope) | Secondary **trope** chips |
| **Teal** `#1f9e94` | Book **spine** accent (card left edge) | Special **glow** (highlighted trope) |
| **Oxblood** `#9a3b2e` | Primary **button** | Book **spine** accent (card left edge) |
| **Gold / gilt** `#c79a3e` | Constant — gilding, stars, genre icons, flourishes | Constant (brighter `#e3b85e`) |

Teal & oxblood swap the *spine* role; violet & teal swap the *glow* role; gold gilds both. (The
"special glow" hue is therefore violet by day, teal by night — intentional.)

---

## 2. Goals

- Replace the generic palette/type with the **Arcane Library** identity across the whole SPA, light
  and dark, primarily by re-defining the existing CSS-variable token blocks.
- Establish a **design-token system**: color, typography, spacing, radius, shadow/elevation, plus
  motif tokens (spine, page-edge, glow, gilt, texture).
- Define a **form language**: the book card, leaf-cut chips, sparing ✦, gilt hairlines.
- Add **atmosphere** (parchment/starfield grounds, leather binding) using **CSS + inline SVG only**
  — no image assets, no new runtime deps.
- Add a **genre iconography** system (gold line-art marks, canonicalized from `Works.genres`).
- Add the **"what's new" ✦ marker** + a per-list "N new since {day}" summary.
- Apply the identity to **shared chrome** and **every view** (including the now-merged `ImportView`).
- Stay **accessible** (WCAG AA, visible focus, `prefers-reduced-motion`, icons carry text labels)
  and **performant** (self-hosted subset fonts, CSS/SVG effects only).

## 3. Non-Goals (YAGNI)

- **No new product features, routes, or data model changes.** The "what's new" marker's v1 signal is
  computed client-side (§8); no schema migration.
- **No raster image/texture assets.** Parchment grain, starfield, and leather are all CSS gradients
  / inline-SVG `feTurbulence` — nothing binary enters the repo or bundle.
- **No animation framework.** Glows/textures are static; only the existing 0.15s theme transition
  remains (disabled under reduced-motion).
- **No commissioned logo.** The brand mark is a typeset Literata wordmark + ✦ glyph.
- **No Non-fiction genre icon in v1** — the catalog is ~99% fiction (data poll, §7.2); revisit if the
  corpus broadens.
- **No per-genre *theming*** (accent shifts by genre) — out of scope, noted for later.

---

## 4. Design tokens

All tokens live in `frontend/src/index.css`. Existing variable **names are preserved** (no component
churn); values change and new tokens are added. Values are the approved hexes from the brainstorm.

### 4.1 Color — existing vars, re-valued

| Token | Light | Dark | Notes |
|---|---|---|---|
| `--bg` | `#f3e8d2` | `#14121d` | Parchment / ink-violet night (carries texture, §6) |
| `--surface` | `#f9efd9` | `#1d1a28` (card grad `#211d2e→#1b1826`) | Raised parchment / card |
| `--surface-2` | `#ecdebf` | `#2a2440` | Sunken / chip ground / nav-active |
| `--text` | `#241f1a` | `#f4ecda` | Ink / parchment-white |
| `--text-soft` | `#2a231b` | `#ece3d2` | Body emphasis |
| `--text-muted` | `#5a4f43` | `#bcb2a3` | Secondary |
| `--text-faint` | `#8a7d6a` | `#6f6757` | Captions/placeholders |
| `--border` | `#e6d4ad` | `#4a3f5e` | Hairlines |
| `--accent` | `#9a3b2e` (oxblood) | `#e3b85e` (gilt) | Primary action — rotates oxblood→gold |
| `--on-accent` | `#fff3ec` | `#1a160f` | Text on primary |
| `--star` | `#c79a3e` | `#e3b85e` | Gilt stars |
| `--chip-bg` / `--chip-fg` | `#e8d29a` / `#7a5e2c` | `rgba(124,92,255,.16)` / `#c7b9ff` | Secondary trope chips (warm gold-tan by day → violet by night). Light deepened from `#ecdebf` — that was too close to `--surface` to read as a pill. **Non-glowing**; the glow is reserved for the special "why" chip. |
| `--chip-genre-bg` | `#e0c685` | `#272e3f` | Legacy token. The History **genre chip** instead uses the **gilt identity** (`color-mix(in srgb, var(--gilt) 22%, var(--surface))` bg + gilt text, bold) so genre reads as *gold* in both themes (genre == gold, §7) and stays distinct from the violet dark tropes. |
| `--badge-new-bg` | `#1f7a6e` (teal) | `#2e8b57` | "New" badge → teal family |
| `--badge-reread-bg` | `#6d4ed6` (violet) | `#7c6bb0` | "Reread" badge → violet family |
| `--on-badge` | `#ffffff` | `#ffffff` | |
| `--topbar-bg` | `#221409` (leather, §6) | `#1a0f08` | Constant dark binding both modes |
| `--topbar-fg` | `#ecd9a6` | `#ecd9a6` | Gilt-parchment wordmark |
| `--topbar-border` | gilt `#c79a3e` | `#b78b3f` | Gilt-tooled bottom edge |
| `--nav-fg` | `#7a6a55` | `#9a8fb0` | Inactive nav |
| `--danger` / `--ok` | `#b23a2b` / `#3f7d4f` | `#f08a7e` / `#79c089` | Warm-tuned |
| `--overlay` / `--menu-shadow` | `rgba(40,25,15,.45)` / `rgba(60,40,20,.18)` | `rgba(0,0,0,.6)` / `rgba(0,0,0,.55)` | |

### 4.2 Color — new tokens

| Token | Light | Dark | Purpose |
|---|---|---|---|
| `--gilt` | `#c79a3e` | `#e3b85e` | Gilding, stars, **genre icons**, flourishes |
| `--spine` | `linear-gradient(180deg,#1f9e94,#14756d)` (teal) | `linear-gradient(180deg,#c64a3a,#8f2f24)` (oxblood) | Card left spine |
| `--spine-glow` | `rgba(31,158,148,.5)` | `rgba(198,74,58,.7)` | Spine glow |
| `--glow` | `#6d4ed6` (violet) | `#45e0d0` (teal) | Special-glow hue + ✦-new marker |
| `--glow-soft` | `rgba(109,78,214,.55)` | `rgba(69,224,208,.6)` | Glow shadow |
| `--chip-special-bg` / `-fg` | `#ece6ff` / `#5b41b8` | `rgba(69,224,208,.12)` / `#7af0e3` | Highlighted "why" trope chip |
| `--page-edge` | `repeating-linear-gradient(90deg,#f6eedb 0 2px,#d8c8a0 2px 4px)` | `repeating-linear-gradient(90deg,rgba(227,184,94,.55) 0 1px,transparent 1px 4px)` | Right fore-edge (cream / gilt-edged) |
| `--marker-new` | `#6d4ed6` | `#b9a6ff` | ✦ "new" |
| `--marker-enriched` | `#1f9e94` | `#5fe6d7` | ✦ "enriched" |

### 4.3 Typography

- **Display / titles:** **Literata** (variable, optical sizing). Title weight **500**; larger
  headers 600; author lines *italic*. Chosen for literary character that stays calm in long lists.
- **Body / UI:** **Inter** (400/500/600).
- Tokens: `--font-display: 'Literata', Georgia, serif;` `--font-body: 'Inter', system-ui, sans-serif;`
- **Scale:** `--fs-display: 1.6rem`, `--fs-title: 1.3rem`, `--fs-body: 1rem`, `--fs-sm: .875rem`,
  `--fs-xs: .6875rem` (eyebrows/labels, uppercase, `letter-spacing:.14em`). Title line-height ~1.15,
  body ~1.5.

### 4.4 Spacing, radius, elevation, motif tokens

- **Spacing** (`--space-1..7`): `4, 8, 12, 16, 20, 24, 32` px.
- **Radius:** `--radius-sharp: 3px` (page/fore-edge), `--radius: 7px` (buttons/inputs),
  `--radius-lg: 14px` (cards/panels), `--radius-spine: 16px` (bound-edge corner),
  `--radius-pill: 999px`, **leaf-cut chip** `--radius-leaf: 4px 12px 4px 12px`.
- **Card book-shape** (locked atom): `border-radius: var(--radius-spine) var(--radius-sharp)
  var(--radius-sharp) var(--radius-spine)`; `overflow:hidden`.
  - `::before` = left **spine**: `width:5px`, full height, `background:var(--spine)`,
    `box-shadow:0 0 10px -2px var(--spine-glow)`.
  - `::after` = right **page fore-edge**: `width:~12px`, full height, flush,
    `border-left:1px solid var(--border)`, `background:var(--page-edge)` (vertical hairlines).
  - `padding-right` clears the page block (~26px).
- **Elevation:** `--shadow-card` (light `0 10px 22px -16px rgba(80,50,20,.5)`, dark
  `0 12px 30px -18px rgba(0,0,0,.8)`), inset top sheen on light cards, `0 0 0 1px rgba(gilt,.14)` ring
  on dark cards.
- **Motifs:** ✦ sparkle (the "what's new" marker, §8), ✦ corner flourish (sparing, `--gilt`), gilt
  hairline divider. Ornament marks special moments only; base UI stays clean.

---

## 5. Form language

- **Book card** — the locked atom above. Trope chips use `--radius-leaf` (leaf-cut). One **special**
  chip per card (`--chip-special-*`) highlights the "why this" trope. Gilt stars. Primary button =
  `--accent`/`--on-accent` + faint gilt hairline; secondary = outline + `--border`, gilt on hover.
- **Genre mark** sits top-right of the card (§7). **✦ marker** + label sit top-left when present (§8).
- Radii, chips, and buttons above are global primitives reused by all views and `ImportView`.

## 6. Atmosphere — textures & binding (CSS/SVG only)

- **Parchment grain (light `--bg`):** an inline-SVG `feTurbulence` (`baseFrequency 0.8`,
  `numOctaves 3`, desaturated, alpha-curved) painted as a `::before` overlay at
  `opacity:.62; mix-blend-mode:multiply`. Tuned with the card shadowing so paper reads without
  hurting text contrast. Cards themselves stay clean.
- **Starfield (dark `--bg`):** layered `radial-gradient`s — a violet + teal nebula glow plus
  scattered 1–1.6px star dots (a few tinted gilt/teal) as a `::before` overlay.
- **Leather binding (top bar):** `linear-gradient(#2c1c17,#20120e)` + a coarser `feTurbulence`
  overlay (`baseFrequency 0.6 0.75`, `mix-blend-mode:overlay; opacity:.55`) + a **gilt-tooled**
  `border-image` bottom edge. Brand = Literata wordmark with a leading gilt ✦.
- All three are pure CSS/SVG (no files); gated so `prefers-reduced-motion` is unaffected (static).

## 7. Genre iconography

### 7.1 Treatment

- **Bare gold line-art** marks (no medallion/disc), `color:var(--gilt)` in both themes,
  ~`20–22px`, top-right corner of the card (clear of the page fore-edge).
- All icons: `viewBox="0 0 24 24"`, `fill:none`, `stroke:currentColor`, `stroke-width:1.6`,
  round joins/caps. Locked path data in **Appendix A**.
- Accessibility: each icon carries a `title`/`aria-label` with the genre name; the icon is decorative
  reinforcement, never the sole carrier of meaning.

### 7.2 Roster (data-driven)

Polled the catalog (FINAL snapshot, 326 works). Genres are noisy (`Works.genres`, 304 distinct with
case dupes + UUID-suffixed slugs); canonicalized to the roster below (counts approximate, consolidated).
**v1 roster (13 + fallback):**

| Genre | Icon | ~count |
|---|---|---|
| Fantasy | castle | ~330 |
| Science Fiction | rocket | ~310 |
| Adventure | map + X | ~234 |
| Young Adult | sprout | ~125 |
| Classics / Literary | feather quill | ~66 |
| LGBTQ | rainbow (no clouds) | ~60 |
| Mystery | magnifier | ~57 |
| War | crossed swords | ~57 |
| Dystopian | barbed wire | ~65 |
| Thriller / Suspense | bolt | ~38 |
| Horror | skull | ~30 |
| Romance | heart | ~20 |
| Historical | column | ~14 |
| **Unknown / other** | **4-point line-art star** (fallback) | — |

A canonicalization map (raw genre slug → canonical key) lives with the icon component; unmapped or
empty → fallback star. **Non-fiction dropped for v1** (near-absent in the corpus).

## 8. The "what's new" ✦ marker

- **Semantics:** the filled, glowing ✦ flags an entry that **changed since the user's last visit** —
  used sparingly (most cards carry none). Distinct from the **outline** 4-point star (unknown-genre
  fallback) and from the corner flourish.
- **Variants:** `--marker-new` (violet) for a brand-new item; `--marker-enriched` (teal) for a book
  whose enrichment just completed. Each pairs with a small uppercase label pill ("New" / "Enriched").
- **Per-list summary:** the view header shows "N new since {day}" (e.g. "2 new since Tuesday").
- **v1 signal (no backend):** *new-since-last-visit* computed client-side — persist the set of
  seen entity ids + a last-visit timestamp in `localStorage`; diff on load.
- **Fast-follow:** *finished-enriching* can read the enrichment status/timestamp now on `main`
  (bulk-import + enrichment-visibility merged). Designed as an additional signal feeding the same
  marker; ships when wired.
- **Exclusion:** a **bulk import never triggers** the marker (it would light up everything) — bulk
  events are excluded from the "changed" set.
- Honors `prefers-reduced-motion` (the glow is a static shadow; no pulsing).

## 9. Component & view application

Restyle via tokens + targeted motif CSS; keep existing structure.

- **Recommendation card** (`RecommendationsView`): the book atom — spine, page edge, leaf-cut trope
  chips, special chip, genre mark, ✦ marker, gilt stars, oxblood/gilt button.
- **Top bar** (`AppShell.css`, `TopBar.tsx`): leather binding (§6), gilt edge, Literata wordmark + ✦.
- **Nav** (`Nav.*`): inactive `--nav-fg`; **active** item gets a spine-style gilt/teal indicator bar
  + `--surface-2` ground.
- **App shell** (`AppShell.css`): parchment/starfield ground; keep the `max-width:820px` reading column.
- **Buttons / chips / book-card**: shared primitives under `frontend/src/components/` so every view
  **and `ImportView`** reuse them.
- **All views** (CSS, + light `.tsx` where needed): `RecommendationsView`, `AnalysisView`, `ChatView`
  (parchment "you" / inked "librarian" bubbles), `AddBookView` (parchment inputs, gilt focus ring,
  leaf chips), `ActivityTrail`, `HistoryView`, and **`ImportView`** (now on `main` — bring it onto the
  tokens/primitives so the import wizard matches).

## 10. Isolation & interfaces

- **`index.css` is the single source of truth** for tokens; components depend only on variable names.
  Changing a hue is a one-line edit.
- **Motif CSS is local** to its owner (book card → `RecommendationsView`; brand → `TopBar`; nav
  indicator → `Nav`). Shared primitives (Button/Chip/BookCard, GenreIcon, NewMarker) expose
  classes/components so consumers don't reimplement spine/page/icon math.
- **`theme.ts` unchanged** — light/dark stays a `data-theme` swap.

## 11. Font delivery — self-host (locked)

Self-host via `@fontsource` (`@fontsource-variable/literata`, `@fontsource-variable/inter` or weight
subsets) imported in `main.tsx`: no third-party runtime dependency (offline / strict-CSP friendly,
matches the app's privacy posture), no FOIT, deterministic builds. Ship only weights used (Literata
400/500/600 + italic, Inter 400/500/600), `font-display: swap`, `<link rel="preload">` the two
primary faces.

## 12. Accessibility & performance

- **Contrast:** every text/bg pair meets **WCAG AA** (≥4.5:1 body, ≥3:1 large); parchment grain is
  background-only so it never sits under body text; muted values verified during build.
- **Meaning never color-only:** genre icons carry text labels; ✦ marker carries a text label/pill;
  badges carry text.
- **Focus:** `:focus-visible` gilt/teal ring on all interactive elements (≥3:1).
- **Motion:** all effects static; `@media (prefers-reduced-motion: reduce)` disables theme/hover
  transitions.
- **Performance:** CSS gradients + inline SVG only (no images/JS for effects); subset, preloaded,
  `swap` fonts.

## 13. Scope, ownership & coordination

Per `.git/AGENT_COORDINATION.md`:

- **bulk-import merged to `main` (PR #55, 2026-06-22); file-holds released.** design-work will
  **rebase onto `main`** to pick up `ImportView`, `App.tsx`, `HistoryView.tsx`, `client.ts` and style
  them. (`client.ts` is data-only; touched only if a view needs the last-visit/seen plumbing.)
- **This branch OWNS** the frontend visual layer: `index.css` (tokens), `theme.ts` (likely
  untouched), shared chrome, all `views/*.css` + their `.tsx` restyles, `ImportView`, new primitives
  under `components/`, `index.html` (font preload), and design docs. No backend changes.

## 14. Testing

- **Existing** `theme.test.ts` + component tests stay green (var names unchanged).
- **Add (light):** token-presence smoke test (core tokens resolve in both themes); a `GenreIcon`
  canonicalization unit test (slug→key, fallback); a `NewMarker`/last-visit util test (new set diff,
  bulk-exclusion).
- **Manual:** walk every view in light & dark; confirm book card, brand/binding, nav active, chips,
  genre icons, ✦ marker, focus rings; before/after screenshots.

## 15. Risks & open questions

- **Global `--bg` warming** touches all views at once (intended); grep view CSS for hardcoded
  `#fff`/grays during build and fold strays into tokens.
- **Font bundle size** — mitigated by weight subsetting; revisit if the woff2 set is larger than
  expected.
- **Mostly resolved:** texture treatment (background grain + starfield ✓), binding (leather ✓), icon
  roster (✓), font (self-host ✓). Remaining minor: final brand wordmark text; exact enrichment-signal
  wiring for the marker fast-follow.

---

## Appendix A — locked icon path data

All: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
stroke-linejoin="round" stroke-linecap="round">…</svg>`, rendered in `--gilt`.

- **Fantasy (castle):** `<path d="M4 20V8H6.5V11H8.5V8H11V11H13V8H15.5V11H17.5V8H20V20Z"/><path d="M10 20v-5a2 2 0 0 1 4 0v5"/>`
- **Sci-Fi (rocket):** `<path d="M12 2.5c2.6 2 4 5.2 4 9 0 2-.9 3.8-2 5H10c-1.1-1.2-2-3-2-5 0-3.8 1.4-7 4-9z"/><circle cx="12" cy="9.5" r="1.5"/><path d="M8.5 16c-1.2 1-1.8 2.6-1.8 4 1.5-.3 2.6-1 3.3-2M15.5 16c1.2 1 1.8 2.6 1.8 4-1.5-.3-2.6-1-3.3-2"/>`
- **Adventure (map+X):** `<path d="M9 4 3 6.5v13.5l6-2.5 6 2.5 6-2.5V3.5L15 6 9 4z"/><path d="M9 4v13.5M15 6v13.5"/><path d="M11.5 10.5l1.5 1.5M13 10.5l-1.5 1.5"/>`
- **Mystery (magnifier):** `<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>`
- **Romance (heart):** `<path d="M12 20S4 14.5 4 9.7A3.8 3.8 0 0 1 12 7a3.8 3.8 0 0 1 8 2.7C20 14.5 12 20 12 20z"/>`
- **Horror (skull):** `<path d="M5 11a7 7 0 0 1 14 0c0 2.3-1 3.6-2.2 4.3V18a1 1 0 0 1-1 1H8.2a1 1 0 0 1-1-1v-2.7C6 14.6 5 13.3 5 11z"/><circle cx="9.6" cy="11.2" r="1.4"/><circle cx="14.4" cy="11.2" r="1.4"/><path d="M11 19v-2M13 19v-2"/>`
- **Thriller (bolt):** `<path d="M13 2 5 13h5l-1 9 8-12h-5l1-8z"/>`
- **Literary (feather quill):** `<path d="M5 19C7 11 11 6.5 19 5c-1 7-5 12-12 13.5z"/><path d="M7.5 17 17 7.5"/><path d="M5 19l-1.6 1.6"/><path d="M9.5 17.6l1.4-1M12 16.8l1.4-1"/>`
- **Historical (column):** `<path d="M5 21h14M6.5 21V9.5M17.5 21V9.5M5 9.5h14M6 9.5 8 6h8l2 3.5M9.5 21V9.5M14.5 21V9.5"/>`
- **Young Adult (sprout):** `<path d="M12 21v-7"/><path d="M12 14c-.5-3-3-4.5-6-4.5.2 3 2.5 5 6 4.5z"/><path d="M12 12c.4-2.6 2.6-4 5.5-3.8C17.3 10.8 15 12.3 12 12z"/>`
- **LGBTQ (rainbow):** `<path d="M3 18a9 9 0 0 1 18 0"/><path d="M6 18a6 6 0 0 1 12 0"/><path d="M9 18a3 3 0 0 1 6 0"/>`
- **War (crossed swords):** `<path d="M6 18Q13.5 11 18.5 5.5"/><path d="M4.7 16.7 7.3 19.3"/><path d="M6 18 4.9 19.1"/><circle cx="4.5" cy="19.5" r=".8"/><path d="M18 18Q10.5 11 5.5 5.5"/><path d="M19.3 16.7 16.7 19.3"/><path d="M18 18 19.1 19.1"/><circle cx="19.5" cy="19.5" r=".8"/>`
- **Dystopian (barbed wire):** `<path d="M2 12H22"/><path d="M6 9.5 8 14.5M8 9.5 6 14.5"/><path d="M11 9.5 13 14.5M13 9.5 11 14.5"/><path d="M16 9.5 18 14.5M18 9.5 16 14.5"/>`
- **Unknown (4-point line star, fallback):** `<path d="M12 3 13.7 10.3 21 12 13.7 13.7 12 21 10.3 13.7 3 12 10.3 10.3Z"/>`
- **✦ "what's new" marker:** the same 4-point star **filled** (`fill:currentColor; stroke:none`) +
  `drop-shadow`/`text-shadow` glow, in `--marker-new` / `--marker-enriched`. (Distinct from the
  outline fallback star above.)
