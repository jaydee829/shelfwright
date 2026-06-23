# Navigation, Tab Icons & Import Affordance Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the mobile bottom-nav disappearing on scroll, replace the emoji tab icons with a bespoke monoline gold icon set, and turn the Import affordance into a cohesive control that a brand-new (empty-history) user can always reach.

**Architecture:** Pure frontend change in the React SPA. A new presentational `LineIcon` component holds all seven monoline glyphs (the five tabs + import + shelf) as inline SVG, consumed by `Nav` and `HistoryView`. Nav gets a `z-index`/safe-area CSS fix and a gold icon treatment. `HistoryView`'s empty-state early return becomes an onboarding panel. No backend, API, route, or token changes — we consume existing CSS variables from `index.css`/`primitives.css`.

**Tech Stack:** React 19, TypeScript, Vite 8, Vitest 4 + Testing Library, react-router, CSS-variable theming.

**Spec:** `docs/superpowers/specs/2026-06-22-nav-tab-import-polish-design.md`

---

## File Structure

- **Create** `frontend/src/components/LineIcon.tsx` — single source of truth for the monoline glyphs (`chat`, `history`, `picks`, `analysis`, `add`, `import`, `shelf`). One `<LineIcon name=… size=… className=… />` component; color via `currentColor`, sized via the `size` prop. Keeps DRY: the `add` glyph is shared between the nav and the empty-state pointer.
- **Create** `frontend/src/components/LineIcon.test.tsx` — unit tests for the component.
- **Create** `frontend/src/components/Nav.test.tsx` — tab links render with accessible names + routes.
- **Modify** `frontend/src/components/Nav.tsx` — `ITEMS` use `LineIconName`; render `<LineIcon>` instead of emoji.
- **Modify** `frontend/src/components/Nav.css` — `z-index` + safe-area (Issue 1); gold icon / muted label / active halo (Issue 2).
- **Modify** `frontend/src/views/HistoryView.tsx` — header ghost import button; empty-state onboarding panel.
- **Modify** `frontend/src/views/HistoryView.css` — ghost button + `.history-empty` panel styles.
- **Modify** `frontend/src/views/HistoryView.test.tsx` — add empty-state onboarding test + header import-button test.

**Conventions:** All test/build commands run **from `frontend/`**. Run a single test file with `npx vitest run <path>`; the whole suite with `npm test`. Lint with `npm run lint`. Commits use `fix(ui):` / `feat(ui):` and end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

---

### Task 1: Mobile bottom-nav stays above scrolling content

**Files:**
- Modify: `frontend/src/components/Nav.css` (mobile `.nav` block ~L14-16 and desktop `@media` block ~L19-26)

Root cause: `.nav` is `position: fixed` with no `z-index`, so `position: relative` `.book-card`s (later in the DOM) paint over it as they scroll under. Lift the bar to `z-index: 10` (matching `.topbar`) and pad for the iOS home indicator.

- [ ] **Step 1: Apply the fix**

In `frontend/src/components/Nav.css`, change the mobile-first `.nav` rule from:

```css
/* Mobile-first: fixed bottom bar; indicator across the top of the active item */
.nav { position: fixed; bottom: 0; left: 0; right: 0; border-top: 1px solid var(--border); }
```

to:

```css
/* Mobile-first: fixed bottom bar; indicator across the top of the active item */
.nav {
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 10;
  border-top: 1px solid var(--border); padding-bottom: env(safe-area-inset-bottom, 0);
}
```

Then, inside the existing `@media (min-width: 768px)` block, add `padding-bottom: 0;` to the `.nav` rule so the desktop left-rail doesn't inherit the safe-area inset. The desktop `.nav` rule becomes:

```css
  .nav {
    position: fixed; top: 56px; bottom: 0; left: 0; right: auto;
    flex-direction: column; width: 88px; border-top: none; border-right: 1px solid var(--border);
    padding-bottom: 0;
  }
```

- [ ] **Step 2: Verify it builds/lints**

Run (from `frontend/`): `npm run lint`
Expected: no errors. (This is a CSS-only change; jsdom does not paint, so there is no meaningful unit test — behaviour is verified in the QC checklist at the end.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Nav.css
git commit -m "fix(ui): keep fixed bottom-nav above scrolling cards (z-index + safe-area)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `LineIcon` component (the monoline glyph set)

**Files:**
- Create: `frontend/src/components/LineIcon.tsx`
- Create: `frontend/src/components/LineIcon.test.tsx`

All glyphs are drawn on a `0 0 24 24` viewBox, `fill="none"`, `stroke="currentColor"`, `stroke-width="1.6"`, round caps/joins, optically centered on y=12 (locked in the spec).

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/LineIcon.test.tsx`:

```tsx
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import LineIcon from './LineIcon'

describe('LineIcon', () => {
  it('renders an aria-hidden svg on a 24-unit viewBox', () => {
    const { container } = render(<LineIcon name="chat" />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg!.getAttribute('viewBox')).toBe('0 0 24 24')
    expect(svg!.getAttribute('aria-hidden')).toBe('true')
  })

  it('renders the analysis glyph as four line segments', () => {
    const { container } = render(<LineIcon name="analysis" />)
    expect(container.querySelectorAll('line').length).toBe(4)
  })

  it('honours the size prop', () => {
    const { container } = render(<LineIcon name="add" size={18} />)
    expect(container.querySelector('svg')!.getAttribute('width')).toBe('18')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/LineIcon.test.tsx`
Expected: FAIL — `Cannot find module './LineIcon'`.

- [ ] **Step 3: Create the component**

Create `frontend/src/components/LineIcon.tsx`:

```tsx
import type { ReactNode } from 'react'

export type LineIconName =
  | 'chat' | 'history' | 'picks' | 'analysis' | 'add' | 'import' | 'shelf'

const PATHS: Record<LineIconName, ReactNode> = {
  chat: <path d="M5 6 h14 a2 2 0 0 1 2 2 v6 a2 2 0 0 1 -2 2 h-9 l-4 3 v-3 H5 a2 2 0 0 1 -2 -2 V8 a2 2 0 0 1 2 -2 z" />,
  history: (
    <>
      <path d="M12 7 c-2 -1.3 -4.6 -1.5 -7 -1 v10 c2.4 -.5 5 -.3 7 1 c2 -1.3 4.6 -1.5 7 -1 V6 c-2.4 -.5 -5 -.3 -7 1 z" />
      <path d="M12 7 V18" />
    </>
  ),
  picks: <path d="M12 6 c.55 4.2 1.85 5.5 6 6 c-4.15 .5 -5.45 1.8 -6 6 c-.55 -4.2 -1.85 -5.5 -6 -6 c4.15 -.5 5.45 -1.8 6 -6 z" />,
  analysis: (
    <>
      <line x1="5" y1="18" x2="19" y2="18" />
      <line x1="7.5" y1="18" x2="7.5" y2="11" />
      <line x1="12" y1="18" x2="12" y2="6" />
      <line x1="16.5" y1="18" x2="16.5" y2="9" />
    </>
  ),
  add: (
    <>
      <circle cx="12" cy="12" r="7.5" />
      <line x1="12" y1="8" x2="12" y2="16" />
      <line x1="8" y1="12" x2="16" y2="12" />
    </>
  ),
  import: (
    <>
      <path d="M5 13 v5 a1 1 0 0 0 1 1 h12 a1 1 0 0 0 1 -1 v-5" />
      <path d="M12 4 V14" />
      <path d="M8.5 10.5 L12 14 L15.5 10.5" />
    </>
  ),
  shelf: (
    <>
      <rect x="3.5" y="6" width="4.5" height="13" rx="1" />
      <rect x="8.8" y="6" width="4.5" height="13" rx="1" />
      <g transform="rotate(-20 17.75 19)">
        <rect x="17.75" y="6" width="4.5" height="13" rx="1" />
      </g>
      <line x1="2.5" y1="20" x2="22.5" y2="20" />
    </>
  ),
}

export default function LineIcon({
  name,
  size = 24,
  className,
}: {
  name: LineIconName
  size?: number
  className?: string
}) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/LineIcon.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/LineIcon.tsx frontend/src/components/LineIcon.test.tsx
git commit -m "feat(ui): add LineIcon monoline glyph set (tabs + import + shelf)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire the icons into the tab bar + gold treatment

**Files:**
- Modify: `frontend/src/components/Nav.tsx`
- Modify: `frontend/src/components/Nav.css` (icon/label/active rules)
- Create: `frontend/src/components/Nav.test.tsx`

Resting bar reads gold (icons carry `--gilt`); labels stay muted ink for legibility (decided at spec review). Active tab keeps gold icon + a warm gold halo + the existing spine-colour indicator; the reserved violet/teal glow is NOT used.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/Nav.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router'
import Nav from './Nav'

describe('Nav', () => {
  it('renders the five primary tabs as links with accessible names', () => {
    render(<Nav />, { wrapper: MemoryRouter })
    for (const name of ['Chat', 'History', 'Picks', 'Analysis', 'Add']) {
      expect(screen.getByRole('link', { name })).toBeInTheDocument()
    }
  })

  it('links each tab to its route', () => {
    render(<Nav />, { wrapper: MemoryRouter })
    expect(screen.getByRole('link', { name: 'Chat' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: 'History' })).toHaveAttribute('href', '/history')
    expect(screen.getByRole('link', { name: 'Picks' })).toHaveAttribute('href', '/recommendations')
    expect(screen.getByRole('link', { name: 'Analysis' })).toHaveAttribute('href', '/analysis')
    expect(screen.getByRole('link', { name: 'Add' })).toHaveAttribute('href', '/add')
  })
})
```

(The glyphs are `aria-hidden`, so each link's accessible name is its label text.)

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/Nav.test.tsx`
Expected: FAIL — the current `Nav.tsx` still renders emoji; the test fails when it imports/asserts, OR passes the name check but you must still replace the emoji. If it passes already on names, proceed — the substantive change is the icon swap; re-run after Step 3 to confirm still green.

- [ ] **Step 3: Replace emoji with `LineIcon` in `Nav.tsx`**

Replace the entire contents of `frontend/src/components/Nav.tsx` with:

```tsx
import { NavLink } from 'react-router'
import LineIcon, { type LineIconName } from './LineIcon'
import './Nav.css'

const ITEMS: { to: string; label: string; icon: LineIconName; end: boolean }[] = [
  { to: '/', label: 'Chat', icon: 'chat', end: true },
  { to: '/history', label: 'History', icon: 'history', end: false },
  { to: '/recommendations', label: 'Picks', icon: 'picks', end: false },
  { to: '/analysis', label: 'Analysis', icon: 'analysis', end: false },
  { to: '/add', label: 'Add', icon: 'add', end: false },
]

export default function Nav() {
  return (
    <nav className="nav" aria-label="Primary">
      {ITEMS.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end} className="nav-item">
          <span className="nav-icon">
            <LineIcon name={item.icon} />
          </span>
          <span className="nav-label">{item.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
```

- [ ] **Step 4: Update the icon/label/active rules in `Nav.css`**

In `frontend/src/components/Nav.css`, replace the existing icon/label/active rules:

```css
.nav-item.active { color: var(--text); font-weight: 600; background: var(--surface-2); }
.nav-item.active::after { /* spine-style indicator */
  content: ""; position: absolute; background: var(--spine); box-shadow: 0 0 8px -2px var(--spine-glow);
}
.nav-icon { font-size: 20px; }
.nav-label { font-size: 12px; }
```

with:

```css
.nav-icon { display: flex; color: var(--gilt); }   /* icons carry the gold in both states */
.nav-icon svg { display: block; }
.nav-label { font-size: 12px; color: var(--nav-fg); }
.nav-item.active { background: var(--surface-2); }
.nav-item.active .nav-label { color: var(--text); font-weight: 600; }
.nav-item.active .nav-icon svg { filter: drop-shadow(0 0 5px color-mix(in srgb, var(--gilt) 70%, transparent)); }
.nav-item.active::after { /* spine-style indicator */
  content: ""; position: absolute; background: var(--spine); box-shadow: 0 0 8px -2px var(--spine-glow);
}
```

(Leave the base `.nav` / `.nav-item` / mobile / desktop blocks — including Task 1's `z-index` and safe-area — unchanged.)

- [ ] **Step 5: Run tests + lint**

Run (from `frontend/`): `npx vitest run src/components/Nav.test.tsx` then `npm run lint`
Expected: PASS (2 tests); lint clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Nav.tsx frontend/src/components/Nav.css frontend/src/components/Nav.test.tsx
git commit -m "feat(ui): bespoke monoline gold tab icons with active halo

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Import header control becomes a ghost button

**Files:**
- Modify: `frontend/src/views/HistoryView.tsx` (header in the populated return ~L57-60; add `LineIcon` import)
- Modify: `frontend/src/views/HistoryView.css` (`.history-import-link` ~L2)
- Modify: `frontend/src/views/HistoryView.test.tsx`

- [ ] **Step 1: Write the failing test**

In `frontend/src/views/HistoryView.test.tsx`, add this test inside the `describe('HistoryView pagination', …)` block:

```tsx
  it('shows the header Import history button linking to /import', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Only Book')])
    renderView()
    await screen.findByText('Only Book')
    expect(screen.getByRole('link', { name: /import history/i })).toHaveAttribute('href', '/import')
  })
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/views/HistoryView.test.tsx -t "Import history button"`
Expected: FAIL — currently the link text is "Import history" but this asserts via role/name + href; if it already matches, it will pass. Either way, proceed to restyle (the visual change is the point); the test guards the link/href stays correct after restyling.

- [ ] **Step 3: Restyle the header link**

In `frontend/src/views/HistoryView.tsx`, add the import near the other imports (top of file):

```tsx
import LineIcon from '../components/LineIcon'
```

Then change the header block from:

```tsx
      <header className="view-head">
        <h2>Reading history</h2>
        <Link to="/import" className="history-import-link">Import history</Link>
      </header>
```

to:

```tsx
      <header className="view-head">
        <h2>Reading history</h2>
        <Link to="/import" className="btn btn--ghost history-import-link">
          <LineIcon name="import" size={18} className="history-import-icon" />
          Import history
        </Link>
      </header>
```

- [ ] **Step 4: Style it in `HistoryView.css`**

In `frontend/src/views/HistoryView.css`, replace:

```css
/* ---- layout ---- */
.history-import-link { font-size: var(--fs-sm); color: var(--text-muted); }
```

with:

```css
/* ---- layout ---- */
/* Ghost button (extends .btn .btn--ghost from primitives); gold import glyph. */
.history-import-link { display: inline-flex; align-items: center; gap: 6px; text-decoration: none; }
.history-import-icon { color: var(--gilt); }
```

- [ ] **Step 5: Run tests + lint**

Run (from `frontend/`): `npx vitest run src/views/HistoryView.test.tsx` then `npm run lint`
Expected: PASS (all HistoryView tests incl. the new one); lint clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/HistoryView.tsx frontend/src/views/HistoryView.css frontend/src/views/HistoryView.test.tsx
git commit -m "feat(ui): style Import history as a ghost button with import glyph

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Empty state becomes an onboarding panel (fixes unreachable import)

**Files:**
- Modify: `frontend/src/views/HistoryView.tsx` (the `items.length === 0` early return ~L53)
- Modify: `frontend/src/views/HistoryView.css` (add `.history-empty` block)
- Modify: `frontend/src/views/HistoryView.test.tsx`

Today the empty state returns before the import link exists, so a new user can't bulk import. Replace it with a panel where import is the primary call to action and a quiet line points to the Add tab.

- [ ] **Step 1: Write the failing test**

In `frontend/src/views/HistoryView.test.tsx`, add inside the describe block:

```tsx
  it('shows an onboarding panel with import + add CTAs when history is empty', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([])
    renderView()
    expect(await screen.findByText(/your shelf is empty/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /import your history/i })).toHaveAttribute('href', '/import')
    expect(screen.getByRole('link', { name: /add a book/i })).toHaveAttribute('href', '/add')
  })
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/views/HistoryView.test.tsx -t "onboarding panel"`
Expected: FAIL — `Unable to find an element with the text: /your shelf is empty/i` (the current empty state renders "Nothing here yet…").

- [ ] **Step 3: Replace the early return with the panel**

In `frontend/src/views/HistoryView.tsx`, change:

```tsx
  if (items.length === 0) return <p>Nothing here yet — finish a book and it'll show up.</p>
```

to:

```tsx
  if (items.length === 0)
    return (
      <div className="history-empty">
        <LineIcon name="shelf" size={56} className="history-empty-seal" />
        <h2 className="history-empty-title">Your shelf is empty</h2>
        <p className="history-empty-body">
          Bring your reading history with you — import it in bulk to get personalised picks right away.
        </p>
        <Link to="/import" className="btn history-empty-cta">
          <LineIcon name="import" size={18} />
          Import your history
        </Link>
        <p className="history-empty-quiet">
          Or just{' '}
          <Link to="/add" className="history-empty-add">
            <LineIcon name="add" size={15} />
            Add a book
          </Link>{' '}
          and it'll show up here.
        </p>
      </div>
    )
```

(The `LineIcon` import was already added in Task 4. If executing tasks out of order, add `import LineIcon from '../components/LineIcon'` at the top.)

- [ ] **Step 4: Style the panel in `HistoryView.css`**

In `frontend/src/views/HistoryView.css`, add this block (e.g. right after the `.history-import-icon` rule):

```css
/* ---- empty-state onboarding panel ---- */
.history-empty { text-align: center; max-width: 30rem; margin: 0 auto; padding: var(--space-7) var(--space-4); }
.history-empty-seal { display: block; margin: 0 auto var(--space-3); color: var(--gilt); }
.history-empty-title { font-family: var(--font-display); font-size: var(--fs-title); margin: 0 0 var(--space-2); }
.history-empty-body { color: var(--text-muted); font-size: var(--fs-sm); margin: 0 auto var(--space-4); }
.history-empty-cta { display: inline-flex; align-items: center; gap: 8px; text-decoration: none; }
.history-empty-quiet {
  display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 5px;
  margin-top: var(--space-4); color: var(--text-faint); font-size: var(--fs-xs);
}
.history-empty-add {
  display: inline-flex; align-items: center; gap: 4px;
  color: var(--text-muted); font-weight: 600; text-decoration: none;
}
.history-empty-add svg { color: var(--gilt); }
```

(The `.history-empty-cta` uses the primary `.btn`; its import glyph rides the button's `--on-accent` text colour automatically. The Add glyph is forced gold via `.history-empty-add svg`.)

- [ ] **Step 5: Run tests + lint**

Run (from `frontend/`): `npx vitest run src/views/HistoryView.test.tsx` then `npm run lint`
Expected: PASS (all HistoryView tests incl. the new onboarding test); lint clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/HistoryView.tsx frontend/src/views/HistoryView.css frontend/src/views/HistoryView.test.tsx
git commit -m "feat(ui): onboarding empty state so new users can reach bulk import

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (controller, after all tasks)

- [ ] **Full suite + lint + typecheck:** from `frontend/`, run `npm test` (all green) and `npm run lint` (clean). Confirm `tsc`/build passes via the project's build script (`npm run build`) if lint doesn't already typecheck.
- [ ] **Visual QC (the committed harness — see `docs/frontend-visual-qc.md` and [[qc-harness]]):** point `qc.tsx` fixtures at (a) a populated history and (b) an empty history (`getHistory → []`), then with Playwright screenshot, in **both themes**:
  - the bottom nav (resting all-gold + the active tab's gold halo + spine indicator) — read the PNGs to confirm centering and that labels are legible muted ink;
  - the History header ghost Import button;
  - the empty-state onboarding panel (shelf seal, title, primary Import button, Add-tab pointer with inline gold ⊕).
- [ ] **Mobile nav behaviour (manual):** at a narrow viewport, scroll the History list and confirm the bottom bar stays on top (no longer painted over by cards).
- [ ] Then proceed to **superpowers:finishing-a-development-branch**.

## Self-review notes (author)

- **Spec coverage:** Issue 1 → Task 1; Issue 2 (glyphs + treatment) → Tasks 2-3; Issue 3A (header button) → Task 4; Issue 3B (onboarding empty state / discoverability fix) → Task 5. Muted-ink labels honoured in Task 3. All glyph path data matches the spec tables exactly.
- **Type consistency:** `LineIconName` defined in Task 2 is imported and used in Task 3 (`Nav.tsx`); `<LineIcon name=… size=… className=… />` signature is identical everywhere it's used (Tasks 3-5).
- **No backend/route changes:** `/import` and `/add` already exist (verified in `App.tsx`).
