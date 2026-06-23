# Navigation, Tab Icons & Import Affordance Polish — Design

**Date:** 2026-06-22
**Status:** Approved (brainstormed with visual companion)
**Surface:** `frontend/` React SPA — Arcane Library visual identity (see `2026-06-21-visual-identity-v2-design.md`)

## Overview

Three related front-end polish items surfaced after Visual Identity v2 shipped, all in the app shell / History view:

1. **Mobile bottom nav disappears on scroll.** The fixed bottom tab bar gets painted over by content as you scroll, so on a phone you can lose the ability to switch tabs. The top bar is unaffected.
2. **Tab icons are off-brand.** The five tabs use multi-colour OS emoji (💬 📚 ✨ 📊 ➕) that clash with the gold line-art genre icons and the ✦ marker.
3. **Import affordance is bland *and* unreachable for new users.** "Import history" is a bare text link in the History header — and it only renders once you *already have* history, because the empty state returns early. A brand-new user (the exact person who most needs bulk import) has no path to it.

These are cosmetic/UX refinements to existing, working features. No backend changes, no API changes, no new routes (the `/import` and `/add` routes already exist).

## Non-goals

- No change to nav structure, tab count, ordering, or routes.
- No change to the import flow itself (`ImportView`) or its API.
- No new icon system for other surfaces; genre icons (`GenreIcon`) are untouched.
- No change to `theme.ts` or the token palette in `index.css` (we *consume* existing tokens).

---

## Issue 1 — Mobile bottom nav hidden on scroll

### Root cause

`.nav` is `position: fixed; bottom: 0` but has **no `z-index`** (`frontend/src/components/Nav.css`). `.book-card` is `position: relative` and appears later in the DOM than `<Nav>` (Nav is rendered before `<main>` in `AppShell.tsx`). Within the same stacking context, positioned elements with `z-index: auto` paint in DOM order, so the positioned cards paint **above** the fixed nav as they scroll underneath it. The top bar survives because it sets `z-index: 10`.

### Fix

Give the fixed nav a stacking position above scrolling content, matching the top bar, and respect the iOS home-indicator safe area:

```css
.nav {
  position: fixed; bottom: 0; left: 0; right: 0;
  z-index: 10;                                   /* NEW — lift above positioned cards */
  border-top: 1px solid var(--border);
  padding-bottom: env(safe-area-inset-bottom, 0); /* NEW — clear the home indicator */
}
```

The desktop rail (`@media (min-width: 768px)`) already sets `position: fixed; top: 56px`; it inherits the new `z-index` (harmless — the topbar at `z-index: 10` and the rail don't overlap) and the safe-area padding is a no-op on desktop. Content bottom padding in `AppShell.css` (`.content { padding-bottom: 88px }`) stays; the safe-area inset only grows the bar by the inset amount on devices that have one, which the 88px already comfortably clears.

### Verification

Headless QC can't easily reproduce momentum scroll, so verify by computed style: `.nav` resolves to `z-index: 10`. The behavioural check is manual (scroll History on a narrow viewport; bar stays on top). A regression-guard unit assertion is low-value here (jsdom doesn't paint), so we rely on the computed-style/visual check rather than a brittle test.

---

## Issue 2 — Bespoke monoline tab icons

Replace the five emoji with a bespoke **monoline gold** icon set in the same language as the genre icons and the ✦ marker. All glyphs are drawn on a `0 0 24 24` viewBox, `fill="none"`, `stroke="currentColor"`, `stroke-width="1.6"`, round caps/joins, and **optically centered on the box midline (y=12)** so they share a baseline across the row.

### Glyphs (final, locked)

| Tab | Metaphor | SVG children |
|---|---|---|
| Chat | speech bubble | `<path d="M5 6 h14 a2 2 0 0 1 2 2 v6 a2 2 0 0 1 -2 2 h-9 l-4 3 v-3 H5 a2 2 0 0 1 -2 -2 V8 a2 2 0 0 1 2 -2 z"/>` |
| History | open book | `<path d="M12 7 c-2 -1.3 -4.6 -1.5 -7 -1 v10 c2.4 -.5 5 -.3 7 1 c2 -1.3 4.6 -1.5 7 -1 V6 c-2.4 -.5 -5 -.3 -7 1 z"/>` + `<path d="M12 7 V18"/>` |
| Picks | ✦ four-point sparkle | `<path d="M12 6 c.55 4.2 1.85 5.5 6 6 c-4.15 .5 -5.45 1.8 -6 6 c-.55 -4.2 -1.85 -5.5 -6 -6 c4.15 -.5 5.45 -1.8 6 -6 z"/>` |
| Analysis | bar chart | `<line x1="5" y1="18" x2="19" y2="18"/>` `<line x1="7.5" y1="18" x2="7.5" y2="11"/>` `<line x1="12" y1="18" x2="12" y2="6"/>` `<line x1="16.5" y1="18" x2="16.5" y2="9"/>` |
| Add | circled plus | `<circle cx="12" cy="12" r="7.5"/>` `<line x1="12" y1="8" x2="12" y2="16"/>` `<line x1="8" y1="12" x2="16" y2="12"/>` |

### Resting & active treatment

Chosen direction (companion option **C**): the bar reads gold at rest; the active tab **stays gold** but is *illuminated* (a warm gold halo) and carries the existing spine-colour top indicator. The reserved violet/teal **glow is NOT used here** — it stays reserved for the future "why this rec" special chip.

- **Resting:** icon stroke = `var(--gilt)`. Icons carry the gold.
- **Resting labels:** keep the existing muted ink (`var(--nav-fg)`) for legibility. *(Note: the approved companion mockup rendered labels gold; gold-on-parchment label text falls below AA contrast at 12px, so the spec keeps labels muted ink and lets the gold icons carry the identity. Flagged for spec review — if gold labels are preferred, we apply them with a darker gilt or a subtle text-shadow.)*
- **Active:** icon stays `var(--gilt)` + `filter: drop-shadow(0 0 5px <warm gold>)`; background `var(--surface-2)`; label → `var(--text)`, weight 600; top indicator `::after` keeps `var(--spine)` (teal light / oxblood dark) with its soft glow — unchanged from today.

### Implementation

Create `frontend/src/components/NavIcon.tsx` — a small presentational component mirroring the `GenreIcon` pattern:

```tsx
type NavIconName = 'chat' | 'history' | 'picks' | 'analysis' | 'add'

const PATHS: Record<NavIconName, ReactNode> = { /* the children above */ }

export default function NavIcon({ name }: { name: NavIconName }) {
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {PATHS[name]}
    </svg>
  )
}
```

`Nav.tsx`: the `ITEMS` array swaps `icon: '💬'` (string) for `icon: 'chat'` (NavIconName); the row renders `<span className="nav-icon"><NavIcon name={item.icon} /></span>` instead of the emoji text. Labels/`aria` unchanged.

`Nav.css`: set icon/active colours per the treatment above (icon colour via `currentColor` on `.nav-icon`; active halo + label/weight; keep the existing `::after` indicator and `.nav-item.active` background).

The **Add** circled-plus glyph is reused in Issue 3's empty-state pointer, so export it in a way both can consume (either `NavIcon name="add"` directly, or factor the raw glyph children into a shared module). Implementer's choice; DRY — define each path once.

---

## Issue 3 — Import affordance: styled control + always reachable

Two surfaces, both in `frontend/src/views/HistoryView.tsx`.

### A. Header control (when history exists)

Replace the bare `<Link className="history-import-link">Import history</Link>` with a cohesive **ghost button** carrying a gold monoline "import" glyph:

- Reuse the primitive ghost-button style (`.btn .btn--ghost`) on the `Link`, plus a small left icon, so it inherits the design system. Keep it in the `.view-head` at the right.
- Import glyph (`ImportIcon`, `0 0 24 24`, gold `var(--gilt)` stroke at `currentColor`, width 1.6):
  `<path d="M5 13 v5 a1 1 0 0 0 1 1 h12 a1 1 0 0 0 1 -1 v-5"/>` `<path d="M12 4 V14"/>` `<path d="M8.5 10.5 L12 14 L15.5 10.5"/>` (tray with a down-arrow).

### B. Empty state → onboarding panel (the fix for the unreachable-import bug)

Today: `if (items.length === 0) return <p>Nothing here yet…</p>` — returns *before* the header that holds the import link. Replace this early return with an onboarding panel so import is always reachable and is the primary first action for a new user:

```
   [ gold bookshelf seal ]
   Your shelf is empty                         (Literata h3)
   Bring your reading history with you — import (muted body)
   it in bulk to get personalised picks right away.
   [ ⤓ Import your history ]                    (primary .btn → /import)
   Or just  ⊕ Add a book  and it'll show up here. (quiet line → /add)
```

- Wrap in a centered `.history-empty` panel.
- **Seal:** `ShelfIcon` (`0 0 24 24`, gold, width 1.5), three equal-size books, the third leaning **left against** the second (top-left corner touching, no overlap):
  `<rect x="3.5" y="6" width="4.5" height="13" rx="1"/>` `<rect x="8.8" y="6" width="4.5" height="13" rx="1"/>` `<g transform="rotate(-20 17.75 19)"><rect x="17.75" y="6" width="4.5" height="13" rx="1"/></g>` `<line x1="2.5" y1="20" x2="22.5" y2="20"/>`
- **Heading:** `Your shelf is empty` (Literata, via existing `h2/h3` display-font rules or a panel class).
- **Body:** `Bring your reading history with you — import it in bulk to get personalised picks right away.`
- **Primary button:** `Link to="/import"` styled as the primary `.btn` (oxblood + gilt ring light / gold dark) with the `ImportIcon`, label **"Import your history"** (Inter — button labels stay in the body font for legibility/consistency; confirmed).
- **Quiet pointer:** a new line below — `Or just <AddIcon/> Add a book and it'll show up here.` where **Add a book** is a `Link to="/add"` and `AddIcon` is the gold circled-plus reused from the nav set. Points the user at the correct tab.

### Styling

Add rules to `frontend/src/views/HistoryView.css`:
- `.history-import-link` → restyle as / replace with the ghost-button treatment (or drop the class and apply `.btn .btn--ghost` + an `.import-icon` rule).
- `.history-empty` panel (centered, padding, max-width on the paragraph), `.history-empty .seal` (≈56px, `color: var(--gilt)`), the quiet line (`.history-empty .quiet` — flex row on its own line, gold inline icon, muted text, the `Add a book` link emphasised).

---

## Files touched

| File | Change |
|---|---|
| `frontend/src/components/Nav.css` | `z-index: 10` + safe-area padding (Issue 1); icon/active gold treatment (Issue 2) |
| `frontend/src/components/Nav.tsx` | `ITEMS` icons → `NavIconName`; render `<NavIcon>` (Issue 2) |
| `frontend/src/components/NavIcon.tsx` | **New** — the five monoline glyphs (Issue 2); also source of the reused Add glyph |
| `frontend/src/views/HistoryView.tsx` | Header ghost import button; replace empty-state early return with onboarding panel (Issue 3) |
| `frontend/src/views/HistoryView.css` | Ghost button + `.history-empty` panel/seal/quiet styles (Issue 3) |
| `frontend/src/components/icons` (import/shelf) | Import & shelf glyphs — new small component(s), location at implementer's discretion (co-located in HistoryView area or a shared `lineIcons` module) |
| `frontend/src/components/Nav.test.tsx` | **New/updated** — nav renders five links with accessible names |
| `frontend/src/views/HistoryView.test.tsx` | Add empty-state test (renders Import CTA + Add pointer); keep header-link coverage updated to the button |

## Testing

- **Nav:** assert the five tab links render with their accessible names (Chat/History/Picks/Analysis/Add) and `to` targets; icons are `aria-hidden` so query by link name.
- **HistoryView empty state:** with `getHistory` resolving `[]`, assert the onboarding panel renders — an "Import your history" control linking to `/import` and an "Add a book" link to `/add`. (This is the behavioural regression guard for the unreachable-import bug.)
- **HistoryView populated:** existing tests keep passing; update any assertion that matched the old "Import history" text link so it matches the new button (still an accessible link/button named "Import history" → `/import`).
- Full `npm test` green; `tsc`/lint clean (icons are pure components — no react-compiler concerns).

## QC (visual)

Use the committed QC harness (`frontend/qc.html` + `qc.tsx`, see `docs/frontend-visual-qc.md`): screenshot the bottom nav (both themes, active state), the History header button, and the empty-state panel (both themes), and read the PNGs back to confirm centering, gold treatment, the active halo/indicator, and the shelf glyph. Manually verify the mobile fixed-nav stays put on scroll at a narrow viewport.
