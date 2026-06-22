# Visual Identity v2 ("Arcane Library") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reskin the React SPA with the "Arcane Library" identity (parchment light / ink-violet dark) across tokens, type, the book-form card, atmosphere, chrome, genre icons, and a sparing "what's new" marker — defined in `docs/superpowers/specs/2026-06-21-visual-identity-v2-design.md`.

**Architecture:** The app already themes via CSS variables in `frontend/src/index.css` (`:root` + `:root[data-theme="dark"]`) toggled by `theme.ts`. The redesign is **mostly a token swap** plus a handful of new, focused units: self-hosted fonts, a `BookCard`/chip/button CSS layer, a `GenreIcon` component (+ canonicalization), a `NewMarker` component (+ a client-side last-visit util), and atmosphere CSS for the shell and top bar. Each view is then restyled by switching to tokens and the shared primitives.

**Tech Stack:** React 19, Vite 8, TypeScript, Vitest 4 + Testing Library, `@fontsource` (Literata + Inter). All visual effects are CSS / inline SVG — no image assets, no new runtime deps beyond fonts.

**Conventions:**
- Run all commands from `frontend/` unless noted. Test runner: `npm test` (vitest run), single file: `npx vitest run src/path/file.test.ts`.
- Frequent commits (one per task). Branch: `worktree-design-work` (already rebased on `main`).
- Icon SVG path data is **locked in spec Appendix A** — copy it verbatim.

---

## File Structure

**New files**
- `frontend/src/components/GenreIcon.tsx` — genre → gilt line-art SVG (+ exported `canonicalizeGenre`).
- `frontend/src/components/GenreIcon.test.tsx` — canonicalization unit tests.
- `frontend/src/components/NewMarker.tsx` — the ✦ "new"/"enriched" marker + label.
- `frontend/src/lib/lastVisit.ts` — client-side "new since last visit" id-diff util.
- `frontend/src/lib/lastVisit.test.ts` — util tests.
- `frontend/src/styles/primitives.css` — shared `.book-card`, `.chip`, `.btn` primitives (imported by `index.css`).

**Modified files**
- `frontend/package.json` — add font deps.
- `frontend/src/main.tsx` — import fonts.
- `frontend/index.html` — `<title>` + optional preload.
- `frontend/src/index.css` — token blocks + base element styles + `@import` primitives.
- `frontend/src/components/{AppShell.css,Nav.css,Nav.tsx,TopBar.tsx}` — atmosphere, binding, nav indicator.
- `frontend/src/components/{SignIn.tsx,NotInvited.tsx}` — token-based styling.
- `frontend/src/views/*.{css,tsx}` — per-view restyle (Recommendations, History, AddBook, Analysis, Chat, ActivityTrail, Import, HistoryEdit).
- `frontend/src/api/client.ts` (+ backend `src/agentic_librarian/api/recommendations`) — **optional** Task 12: surface `genres` so icons light up with real data.

---

## Phase 1 — Foundation (fonts + tokens)

### Task 1: Self-host fonts

**Files:**
- Modify: `frontend/package.json` (deps)
- Modify: `frontend/src/main.tsx:1-6`
- Modify: `frontend/index.html:7`

- [ ] **Step 1: Install font packages**

Run (from `frontend/`):
```bash
npm install @fontsource-variable/literata @fontsource-variable/inter
```
Expected: both added to `dependencies`, `package-lock.json` updated.

- [ ] **Step 2: Import fonts in `main.tsx`**

Add these imports above `import './index.css'` (so tokens can reference the families):
```tsx
import '@fontsource-variable/inter'
import '@fontsource-variable/literata'
import './index.css'
```

- [ ] **Step 3: Fix the document title**

In `frontend/index.html` change `<title>frontend</title>` to:
```html
<title>The Librarian</title>
```

- [ ] **Step 4: Verify build + tests still pass**

Run: `npm run build && npm test`
Expected: build succeeds; existing tests pass (fonts don't change behavior).

- [ ] **Step 5: Commit**
```bash
git add package.json package-lock.json src/main.tsx index.html
git commit -m "feat(ui): self-host Literata + Inter fonts"
```

---

### Task 2: Rewrite the token blocks + base styles

**Files:**
- Modify: `frontend/src/index.css` (entire file)

- [ ] **Step 1: Replace `index.css` with the Arcane Library tokens**

Replace the whole file with (values from spec §4):
```css
@import './styles/primitives.css';

:root {
  /* surfaces & text */
  --bg: #f3e8d2; --surface: #f9efd9; --surface-2: #ecdebf;
  --text: #241f1a; --text-soft: #2a231b; --text-muted: #5a4f43; --text-faint: #8a7d6a;
  --border: #e6d4ad;
  /* action / accents */
  --accent: #9a3b2e; --on-accent: #fff3ec; --on-danger: #fff3ec; --on-badge: #ffffff;
  --gilt: #c79a3e; --star: #c79a3e;
  --glow: #6d4ed6; --glow-soft: rgba(109,78,214,.55);
  --spine: linear-gradient(180deg,#1f9e94,#14756d); --spine-glow: rgba(31,158,148,.5);
  --page-edge: repeating-linear-gradient(90deg,#f6eedb 0 2px,#d8c8a0 2px 4px);
  --marker-new: #6d4ed6; --marker-enriched: #1f9e94;
  /* chips & badges */
  --chip-bg: #ecdebf; --chip-fg: #856a3f; --chip-genre-bg: #e7d9b8;
  --chip-special-bg: #ece6ff; --chip-special-fg: #5b41b8;
  --badge-new-bg: #1f7a6e; --badge-reread-bg: #6d4ed6;
  /* chrome */
  --strong-bg: #221409; --strong-fg: #ecd9a6;
  --topbar-bg: #221409; --topbar-fg: #ecd9a6; --topbar-border: #c79a3e; --nav-fg: #7a6a55;
  /* semantic */
  --danger: #b23a2b; --ok: #3f7d4f; --done-mark: #1f7a6e;
  --overlay: rgba(40,25,15,.45); --menu-shadow: rgba(60,40,20,.18);
  /* type */
  --font-display: 'Literata Variable', Georgia, serif;
  --font-body: 'Inter Variable', system-ui, -apple-system, sans-serif;
  --fs-display: 1.6rem; --fs-title: 1.3rem; --fs-body: 1rem; --fs-sm: .875rem; --fs-xs: .6875rem;
  /* space & radius */
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-5: 20px; --space-6: 24px; --space-7: 32px;
  --radius-sharp: 3px; --radius: 7px; --radius-lg: 14px; --radius-spine: 16px; --radius-pill: 999px;
  --radius-leaf: 4px 12px 4px 12px;
  --shadow-card: 0 10px 22px -16px rgba(80,50,20,.5);
  /* atmosphere */
  --paper: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='p'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3CfeComponentTransfer%3E%3CfeFuncA type='linear' slope='0.6'/%3E%3C/feComponentTransfer%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23p)'/%3E%3C/svg%3E");
}

:root[data-theme="dark"] {
  --bg: #14121d; --surface: #1d1a28; --surface-2: #2a2440;
  --text: #f4ecda; --text-soft: #ece3d2; --text-muted: #bcb2a3; --text-faint: #6f6757;
  --border: #4a3f5e;
  --accent: #e3b85e; --on-accent: #1a160f; --on-danger: #1a160f; --on-badge: #ffffff;
  --gilt: #e3b85e; --star: #e3b85e;
  --glow: #45e0d0; --glow-soft: rgba(69,224,208,.6);
  --spine: linear-gradient(180deg,#c64a3a,#8f2f24); --spine-glow: rgba(198,74,58,.7);
  --page-edge: repeating-linear-gradient(90deg,rgba(227,184,94,.55) 0 1px,transparent 1px 4px);
  --marker-new: #b9a6ff; --marker-enriched: #5fe6d7;
  --chip-bg: rgba(124,92,255,.16); --chip-fg: #c7b9ff; --chip-genre-bg: #272e3f;
  --chip-special-bg: rgba(69,224,208,.12); --chip-special-fg: #7af0e3;
  --badge-new-bg: #2e8b57; --badge-reread-bg: #7c6bb0;
  --strong-bg: #2a2440; --strong-fg: #ece3d2;
  --topbar-bg: #1a0f08; --topbar-fg: #ecd9a6; --topbar-border: #b78b3f; --nav-fg: #9a8fb0;
  --danger: #f08a7e; --ok: #79c089; --done-mark: #66bb6a;
  --overlay: rgba(0,0,0,.6); --menu-shadow: rgba(0,0,0,.55);
  --shadow-card: 0 12px 30px -18px rgba(0,0,0,.8);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: var(--font-body);
  background: var(--bg);
  color: var(--text);
  transition: background-color .15s ease, color .15s ease;
}
/* parchment grain on the light ground only (background-only; never under body text directly) */
body::before {
  content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none;
  background-image: var(--paper); mix-blend-mode: multiply; opacity: .62;
}
:root[data-theme="dark"] body::before {
  /* starfield: nebula glow + scattered stars */
  background-image:
    radial-gradient(60% 50% at 80% 0%, rgba(124,92,255,.16), transparent 60%),
    radial-gradient(50% 50% at 5% 100%, rgba(69,224,208,.12), transparent 60%),
    radial-gradient(1.5px 1.5px at 14% 24%, rgba(255,255,255,.8), transparent),
    radial-gradient(1.6px 1.6px at 82% 54%, rgba(69,224,208,.8), transparent),
    radial-gradient(1.5px 1.5px at 92% 30%, rgba(227,184,94,.85), transparent),
    radial-gradient(1.2px 1.2px at 36% 74%, rgba(255,255,255,.5), transparent),
    radial-gradient(1.3px 1.3px at 60% 88%, rgba(255,255,255,.45), transparent);
  mix-blend-mode: normal; opacity: .9;
}
h1, h2, h3 { font-family: var(--font-display); font-weight: 600; color: var(--text); }
h2 { font-size: var(--fs-display); }
button { font: inherit; cursor: pointer; }
:focus-visible { outline: 2px solid var(--gilt); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  body, * { transition: none !important; }
}
```

- [ ] **Step 2: Create the (initially empty) primitives file so the `@import` resolves**

Create `frontend/src/styles/primitives.css`:
```css
/* Shared primitives — populated in Tasks 3, 6, 10. */
```

- [ ] **Step 3: Verify**

Run: `npm test`
Expected: all existing tests pass (var names unchanged; `theme.test.ts` green).

- [ ] **Step 4: Manually eyeball**

Run: `npm run dev`, open the app, toggle theme. Expected: parchment light + ink-violet dark grounds, serif headings. (Cards still look default — restyled later.)

- [ ] **Step 5: Commit**
```bash
git add src/index.css src/styles/primitives.css
git commit -m "feat(ui): Arcane Library design tokens + parchment/starfield ground"
```

---

## Phase 2 — Shared primitives & components

### Task 3: `book-card` + `chip` + `btn` primitives

**Files:**
- Modify: `frontend/src/styles/primitives.css`

- [ ] **Step 1: Write the primitives CSS**

Replace `primitives.css` with:
```css
/* ---- book-form card ---- */
.book-card {
  position: relative; overflow: hidden;
  background: var(--surface); color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-spine) var(--radius-sharp) var(--radius-sharp) var(--radius-spine);
  padding: var(--space-3) 28px var(--space-3) var(--space-4);
  box-shadow: 0 1px 0 rgba(255,255,255,.6) inset, var(--shadow-card);
}
:root[data-theme="dark"] .book-card {
  background: linear-gradient(180deg,#211d2e,#1b1826);
  box-shadow: 0 0 0 1px rgba(227,184,94,.14), var(--shadow-card);
}
.book-card::before { /* spine */
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 5px;
  background: var(--spine); box-shadow: 0 0 10px -2px var(--spine-glow);
}
.book-card::after { /* page fore-edge */
  content: ""; position: absolute; right: 0; top: 0; bottom: 0; width: 12px;
  border-left: 1px solid var(--border); background: var(--page-edge);
}

/* ---- chips ---- */
.chip {
  display: inline-block; font-size: var(--fs-xs); font-weight: 500;
  padding: 3px 9px; border-radius: var(--radius-leaf);
  background: var(--chip-bg); color: var(--chip-fg);
}
:root[data-theme="dark"] .chip { box-shadow: 0 0 0 1px rgba(124,92,255,.28) inset; }
.chip--special {
  font-weight: 600; background: var(--chip-special-bg); color: var(--chip-special-fg);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--glow) 35%, transparent), 0 0 10px -2px var(--glow-soft);
}

/* ---- buttons ---- */
.btn {
  border: none; font: 600 var(--fs-sm)/1 var(--font-body);
  padding: 8px 16px; border-radius: var(--radius); cursor: pointer;
  background: var(--accent); color: var(--on-accent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--gilt) 45%, transparent);
}
.btn--ghost {
  background: transparent; color: var(--text);
  border: 1px solid var(--border); box-shadow: none;
}
.btn:disabled { opacity: .5; cursor: not-allowed; }
```

- [ ] **Step 2: Verify build (CSS compiles, classes available)**

Run: `npm run build`
Expected: success. (`color-mix` is supported by Vite's target browsers; if your `browserslist` is older, replace the `color-mix(...)` boxes with the literal `rgba(199,154,62,.45)` / `rgba(...)` from spec.)

- [ ] **Step 3: Commit**
```bash
git add src/styles/primitives.css
git commit -m "feat(ui): book-card, chip, and button primitives"
```

---

### Task 4: `GenreIcon` component + canonicalization (TDD)

**Files:**
- Create: `frontend/src/components/GenreIcon.tsx`
- Test: `frontend/src/components/GenreIcon.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `GenreIcon.test.tsx`:
```tsx
import { render } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { GenreIcon, canonicalizeGenre } from './GenreIcon'

describe('canonicalizeGenre', () => {
  it('maps urban-fantasy to fantasy', () => expect(canonicalizeGenre(['urban-fantasy'])).toBe('fantasy'))
  it('strips UUID suffix slugs', () => expect(canonicalizeGenre(['high-fantasy-dfcd50f5-2789-45fc-9ee3-3e4354620e62'])).toBe('fantasy'))
  it('maps science-fiction to scifi', () => expect(canonicalizeGenre(['science-fiction'])).toBe('scifi'))
  it('prefers dystopian over scifi', () => expect(canonicalizeGenre(['science-fiction', 'dystopian'])).toBe('dystopian'))
  it('maps classics to literary', () => expect(canonicalizeGenre(['classics'])).toBe('literary'))
  it('returns null for unknown/empty', () => {
    expect(canonicalizeGenre(['general'])).toBeNull()
    expect(canonicalizeGenre([])).toBeNull()
  })
})

describe('GenreIcon', () => {
  it('renders an svg with the genre name as accessible label', () => {
    const { container } = render(<GenreIcon genres={['fantasy']} />)
    const svg = container.querySelector('svg')
    expect(svg).toBeTruthy()
    expect(svg?.getAttribute('aria-label')).toBe('Fantasy')
  })
  it('renders the fallback star for unknown genre', () => {
    const { container } = render(<GenreIcon genres={['general']} />)
    expect(container.querySelector('svg')?.getAttribute('aria-label')).toBe('Other')
  })
})
```

- [ ] **Step 2: Run it; verify it fails**

Run: `npx vitest run src/components/GenreIcon.test.tsx`
Expected: FAIL ("Failed to resolve import './GenreIcon'").

- [ ] **Step 3: Implement `GenreIcon.tsx`**

Copy the path data **verbatim from spec Appendix A**:
```tsx
const PATTERNS: [string, RegExp][] = [
  ['fantasy', /fantas|wuxia/],
  ['dystopian', /dystop/],
  ['scifi', /sci-?fi|science.?fiction|\bspace\b|alien/],
  ['horror', /horror|occult|paranormal/],
  ['mystery', /mystery|crime|detective|noir/],
  ['thriller', /thriller|suspense/],
  ['romance', /romance/],
  ['war', /\bwar\b|military/],
  ['lgbtq', /lgbtq|queer/],
  ['young-adult', /young.?adult|\bya\b/],
  ['historical', /historical|\bhistory\b/],
  ['literary', /literary|literature|classic/],
  ['adventure', /adventur|action/],
]

const LABELS: Record<string, string> = {
  fantasy: 'Fantasy', scifi: 'Science Fiction', adventure: 'Adventure', mystery: 'Mystery',
  romance: 'Romance', horror: 'Horror', thriller: 'Thriller', literary: 'Literary',
  historical: 'Historical', 'young-adult': 'Young Adult', lgbtq: 'LGBTQ', war: 'War',
  dystopian: 'Dystopian', other: 'Other',
}

// path data copied verbatim from spec Appendix A
const PATHS: Record<string, string> = {
  fantasy: '<path d="M4 20V8H6.5V11H8.5V8H11V11H13V8H15.5V11H17.5V8H20V20Z"/><path d="M10 20v-5a2 2 0 0 1 4 0v5"/>',
  scifi: '<path d="M12 2.5c2.6 2 4 5.2 4 9 0 2-.9 3.8-2 5H10c-1.1-1.2-2-3-2-5 0-3.8 1.4-7 4-9z"/><circle cx="12" cy="9.5" r="1.5"/><path d="M8.5 16c-1.2 1-1.8 2.6-1.8 4 1.5-.3 2.6-1 3.3-2M15.5 16c1.2 1 1.8 2.6 1.8 4-1.5-.3-2.6-1-3.3-2"/>',
  adventure: '<path d="M9 4 3 6.5v13.5l6-2.5 6 2.5 6-2.5V3.5L15 6 9 4z"/><path d="M9 4v13.5M15 6v13.5"/><path d="M11.5 10.5l1.5 1.5M13 10.5l-1.5 1.5"/>',
  mystery: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>',
  romance: '<path d="M12 20S4 14.5 4 9.7A3.8 3.8 0 0 1 12 7a3.8 3.8 0 0 1 8 2.7C20 14.5 12 20 12 20z"/>',
  horror: '<path d="M5 11a7 7 0 0 1 14 0c0 2.3-1 3.6-2.2 4.3V18a1 1 0 0 1-1 1H8.2a1 1 0 0 1-1-1v-2.7C6 14.6 5 13.3 5 11z"/><circle cx="9.6" cy="11.2" r="1.4"/><circle cx="14.4" cy="11.2" r="1.4"/><path d="M11 19v-2M13 19v-2"/>',
  thriller: '<path d="M13 2 5 13h5l-1 9 8-12h-5l1-8z"/>',
  literary: '<path d="M5 19C7 11 11 6.5 19 5c-1 7-5 12-12 13.5z"/><path d="M7.5 17 17 7.5"/><path d="M5 19l-1.6 1.6"/><path d="M9.5 17.6l1.4-1M12 16.8l1.4-1"/>',
  historical: '<path d="M5 21h14M6.5 21V9.5M17.5 21V9.5M5 9.5h14M6 9.5 8 6h8l2 3.5M9.5 21V9.5M14.5 21V9.5"/>',
  'young-adult': '<path d="M12 21v-7"/><path d="M12 14c-.5-3-3-4.5-6-4.5.2 3 2.5 5 6 4.5z"/><path d="M12 12c.4-2.6 2.6-4 5.5-3.8C17.3 10.8 15 12.3 12 12z"/>',
  lgbtq: '<path d="M3 18a9 9 0 0 1 18 0"/><path d="M6 18a6 6 0 0 1 12 0"/><path d="M9 18a3 3 0 0 1 6 0"/>',
  war: '<path d="M6 18Q13.5 11 18.5 5.5"/><path d="M4.7 16.7 7.3 19.3"/><path d="M6 18 4.9 19.1"/><circle cx="4.5" cy="19.5" r=".8"/><path d="M18 18Q10.5 11 5.5 5.5"/><path d="M19.3 16.7 16.7 19.3"/><path d="M18 18 19.1 19.1"/><circle cx="19.5" cy="19.5" r=".8"/>',
  other: '<path d="M12 3 13.7 10.3 21 12 13.7 13.7 12 21 10.3 13.7 3 12 10.3 10.3Z"/>',
}

function strip(g: string): string {
  return g.toLowerCase().replace(/-[0-9a-f-]{20,}$/, '').trim()
}

export function canonicalizeGenre(genres: string[] | undefined): string | null {
  if (!genres?.length) return null
  const norm = genres.map(strip)
  for (const [key, re] of PATTERNS) if (norm.some((g) => re.test(g))) return key
  return null
}

export function GenreIcon({ genres, className }: { genres?: string[]; className?: string }) {
  const key = canonicalizeGenre(genres) ?? 'other'
  return (
    <svg
      className={className}
      viewBox="0 0 24 24" width="22" height="22"
      fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinejoin="round" strokeLinecap="round"
      role="img" aria-label={LABELS[key]}
      dangerouslySetInnerHTML={{ __html: PATHS[key] }}
    />
  )
}
```

- [ ] **Step 4: Run tests; verify they pass**

Run: `npx vitest run src/components/GenreIcon.test.tsx`
Expected: PASS (8 assertions).

- [ ] **Step 5: Commit**
```bash
git add src/components/GenreIcon.tsx src/components/GenreIcon.test.tsx
git commit -m "feat(ui): GenreIcon component + genre canonicalization"
```

---

### Task 5: `lastVisit` util + `NewMarker` (TDD)

**Files:**
- Create: `frontend/src/lib/lastVisit.ts`, `frontend/src/lib/lastVisit.test.ts`
- Create: `frontend/src/components/NewMarker.tsx`

- [ ] **Step 1: Write the failing util test**

Create `frontend/src/lib/lastVisit.test.ts`:
```ts
import { beforeEach, describe, it, expect } from 'vitest'
import { computeNewIds, markSeen } from './lastVisit'

beforeEach(() => localStorage.clear())

describe('computeNewIds', () => {
  it('treats all ids as new on first visit', () => {
    expect(computeNewIds('recs', ['a', 'b'])).toEqual(new Set(['a', 'b']))
  })
  it('returns only unseen ids after markSeen', () => {
    markSeen('recs', ['a', 'b'])
    expect(computeNewIds('recs', ['a', 'b', 'c'])).toEqual(new Set(['c']))
  })
  it('is namespaced by key', () => {
    markSeen('recs', ['a'])
    expect(computeNewIds('history', ['a'])).toEqual(new Set(['a']))
  })
})
```

- [ ] **Step 2: Run it; verify it fails**

Run: `npx vitest run src/lib/lastVisit.test.ts`
Expected: FAIL ("Failed to resolve import './lastVisit'").

- [ ] **Step 3: Implement `lastVisit.ts`**

```ts
const PREFIX = 'seen:'

function read(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    return raw ? new Set(JSON.parse(raw) as string[]) : new Set()
  } catch {
    return new Set()
  }
}

/** Ids present now that were NOT seen on a previous visit. */
export function computeNewIds(key: string, ids: string[]): Set<string> {
  const seen = read(key)
  return new Set(ids.filter((id) => !seen.has(id)))
}

/** Record the currently-shown ids as seen (call after rendering, e.g. in an effect). */
export function markSeen(key: string, ids: string[]): void {
  try {
    const merged = new Set([...read(key), ...ids])
    localStorage.setItem(PREFIX + key, JSON.stringify([...merged]))
  } catch {
    /* storage unavailable — degrade to no marker */
  }
}
```

- [ ] **Step 4: Run tests; verify they pass**

Run: `npx vitest run src/lib/lastVisit.test.ts`
Expected: PASS (3 assertions).

- [ ] **Step 5: Implement `NewMarker.tsx`** (no separate test — exercised via RecommendationsView in Task 10)

```tsx
import './NewMarker.css'

const STAR = '<path d="M12 3 13.7 10.3 21 12 13.7 13.7 12 21 10.3 13.7 3 12 10.3 10.3Z"/>'

export function NewMarker({ kind }: { kind: 'new' | 'enriched' }) {
  const label = kind === 'new' ? 'New' : 'Enriched'
  return (
    <span className={`new-marker new-marker--${kind}`}>
      <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"
        role="img" aria-label={label} dangerouslySetInnerHTML={{ __html: STAR }} />
      <span className="new-marker__label">{label}</span>
    </span>
  )
}
```

Create `frontend/src/components/NewMarker.css`:
```css
.new-marker { display: inline-flex; align-items: center; gap: 6px; }
.new-marker > svg { filter: drop-shadow(0 0 6px currentColor); }
.new-marker--new > svg { color: var(--marker-new); }
.new-marker--enriched > svg { color: var(--marker-enriched); }
.new-marker__label {
  font: 600 9px/1 var(--font-body); letter-spacing: .12em; text-transform: uppercase;
  padding: 2px 6px; border-radius: var(--radius-pill);
}
.new-marker--new .new-marker__label { color: var(--chip-special-fg); background: var(--chip-special-bg); }
.new-marker--enriched .new-marker__label { color: #14756d; background: #dff3f0; }
:root[data-theme="dark"] .new-marker--enriched .new-marker__label { color: #7af0e3; background: rgba(69,224,208,.14); }
@media (prefers-reduced-motion: reduce) { .new-marker > svg { filter: none; } }
```

- [ ] **Step 6: Commit**
```bash
git add src/lib/lastVisit.ts src/lib/lastVisit.test.ts src/components/NewMarker.tsx src/components/NewMarker.css
git commit -m "feat(ui): lastVisit util + NewMarker (what's-new ✦)"
```

---

## Phase 3 — Chrome

### Task 6: Leather binding top bar

**Files:**
- Modify: `frontend/src/components/AppShell.css:1-12`
- Modify: `frontend/src/components/TopBar.tsx:17-18`

- [ ] **Step 1: Restyle the top bar in `AppShell.css`**

Replace the `.topbar*` rules (keep the `.content` rules below them unchanged except as Task 7 notes):
```css
.topbar {
  position: fixed; top: 0; left: 0; right: 0; height: 56px; z-index: 10;
  display: flex; align-items: center; justify-content: space-between; padding: 0 16px;
  color: var(--topbar-fg);
  background: linear-gradient(180deg,#2c1c17,#20120e);
  border-bottom: 2px solid; border-image: linear-gradient(90deg,#8a6a2e,var(--gilt),#8a6a2e) 1;
}
.topbar::before { /* leather grain */
  content: ""; position: absolute; inset: 0; pointer-events: none; mix-blend-mode: overlay; opacity: .5;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='220' height='90'%3E%3Cfilter id='l'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.6 0.75' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23l)'/%3E%3C/svg%3E");
}
.topbar-title { position: relative; z-index: 1; font-family: var(--font-display); font-weight: 600; }
.topbar-title::before { content: "✦ "; color: var(--gilt); }
.topbar-right { position: relative; z-index: 1; display: flex; align-items: center; gap: 12px; }
.avatar {
  display: grid; place-items: center; width: 32px; height: 32px; border-radius: 50%;
  background: var(--accent); color: var(--on-accent); font-weight: 600; box-shadow: 0 0 0 1px rgba(227,184,94,.5);
}
.topbar-right button, .theme-toggle {
  background: transparent; color: var(--topbar-fg);
  border: 1px solid var(--topbar-border); border-radius: var(--radius); padding: 4px 10px;
}
```

- [ ] **Step 2: Verify the wordmark renders the ✦ once**

`TopBar.tsx` already renders `<span className="topbar-title">The Librarian</span>` — the `::before` adds the gilt ✦. No `.tsx` change needed unless the ✦ should be selectable text (leave as CSS).

- [ ] **Step 3: Verify tests + visual**

Run: `npm test` then `npm run dev`. Expected: `TopBar.test.tsx` passes; binding shows leather grain + gilt edge + "✦ The Librarian" in Literata.

- [ ] **Step 4: Commit**
```bash
git add src/components/AppShell.css
git commit -m "feat(ui): leather binding top bar with gilt edge + wordmark"
```

---

### Task 7: App shell reading column + Nav active indicator

**Files:**
- Modify: `frontend/src/components/Nav.css` (whole file)

- [ ] **Step 1: Restyle `Nav.css`**

```css
.nav { display: flex; background: var(--surface); }
.nav-item {
  position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px; padding: 10px; text-decoration: none; color: var(--nav-fg); flex: 1;
  font-family: var(--font-body);
}
.nav-item.active { color: var(--text); font-weight: 600; background: var(--surface-2); }
.nav-item.active::after { /* spine-style indicator */
  content: ""; position: absolute; background: var(--spine); box-shadow: 0 0 8px -2px var(--spine-glow);
}
.nav-icon { font-size: 20px; }
.nav-label { font-size: 12px; }

/* Mobile-first: fixed bottom bar; indicator across the top of the active item */
.nav { position: fixed; bottom: 0; left: 0; right: 0; border-top: 1px solid var(--border); }
.nav-item.active::after { left: 0; right: 0; top: 0; height: 3px; }

/* Desktop: left icon rail; indicator down the left edge */
@media (min-width: 768px) {
  .nav {
    position: fixed; top: 56px; bottom: 0; left: 0; right: auto;
    flex-direction: column; width: 88px; border-top: none; border-right: 1px solid var(--border);
  }
  .nav-item { flex: 0; }
  .nav-item.active::after { top: 0; bottom: 0; left: 0; right: auto; width: 3px; height: auto; }
}
```

- [ ] **Step 2: Verify + commit**

Run: `npm test && npm run dev` (confirm active nav item shows the gilt/teal spine indicator).
```bash
git add src/components/Nav.css
git commit -m "feat(ui): nav active spine indicator + tokens"
```

---

## Phase 4 — Views

> Each view task: switch hardcoded colors to tokens, apply primitives, verify its `*.test.tsx` stays green, eyeball in `npm run dev`, commit. Restyles must not change DOM the tests assert on (text, roles, labels). When a class is renamed, update the matching `.test.tsx` query in the same task.

### Task 8: Recommendations view — book cards + genre icon + ✦ marker + summary

**Files:**
- Modify: `frontend/src/views/RecommendationsView.tsx`
- Modify: `frontend/src/views/RecommendationsView.css` (whole file)
- Modify: `frontend/src/api/client.ts` (add optional `genres` to the `Recommendation` type)

- [ ] **Step 1: Add optional `genres` to the `Recommendation` type**

In `client.ts`, add `genres?: string[]` to the `Recommendation` interface (icons fall back to the star until the API provides it — see Task 12).

- [ ] **Step 2: Update `RecommendationsView.tsx`**

Render genre icon, the ✦ "new" marker (from `computeNewIds`), the "N new since…" summary, and the book-card primitive. Keep existing button text/handlers so `RecommendationsView.test.tsx` still matches.
```tsx
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { getRecommendations, setRecommendationStatus, type Recommendation } from '../api/client'
import { GenreIcon } from '../components/GenreIcon'
import { NewMarker } from '../components/NewMarker'
import { computeNewIds, markSeen } from '../lib/lastVisit'
import './RecommendationsView.css'

function ReadBadge({ r }: { r: Recommendation }) {
  if (r.read_status === 'reread') {
    const year = r.last_read ? r.last_read.slice(0, 4) : null
    const stars = r.rating ? ` · ${'★'.repeat(r.rating)}` : ''
    return <span className="rec-badge reread">{year ? `Re-read · ${year}${stars}` : `Re-read${stars}`}</span>
  }
  if (r.read_status === 'new') return <span className="rec-badge new">New</span>
  return null
}

export default function RecommendationsView() {
  const navigate = useNavigate()
  const [recs, setRecs] = useState<Recommendation[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const newIds = useMemo(() => (recs ? computeNewIds('recs', recs.map((r) => r.id)) : new Set<string>()), [recs])

  useEffect(() => { void getRecommendations().then(setRecs) }, [])
  useEffect(() => { if (recs) markSeen('recs', recs.map((r) => r.id)) }, [recs])

  async function dismiss(id: string) {
    setBusy(id)
    try {
      await setRecommendationStatus(id, 'Dismissed')
      setRecs((cur) => (cur ? cur.filter((r) => r.id !== id) : cur))
    } finally { setBusy(null) }
  }
  function readThis(r: Recommendation) {
    navigate('/add', { state: { title: r.title, author: r.authors.join(', '), suggestionId: r.id } })
  }

  if (recs === null) return <p>Loading…</p>
  if (recs.length === 0) return <p>No recommendations right now — ask the Librarian in Chat for ideas.</p>

  return (
    <div>
      <header className="view-head">
        <h2>Recommendations</h2>
        {newIds.size > 0 && <span className="view-head__summary">{newIds.size} new</span>}
      </header>
      <div className="rec-list">
        {recs.map((r) => (
          <article key={r.id} className="book-card rec-card">
            <GenreIcon className="rec-genre" genres={r.genres} />
            {newIds.has(r.id) && <NewMarker kind="new" />}
            <div className="rec-head">
              <span className="rec-title">{r.title}</span>
              <span className="rec-authors">{r.authors.join(', ')}</span>
              <ReadBadge r={r} />
            </div>
            {r.justification && <p className="rec-why">{r.justification}</p>}
            <div className="rec-actions">
              <button className="btn" onClick={() => readThis(r)}>✓ I read this</button>
              <button className="btn btn--ghost" onClick={() => void dismiss(r.id)} disabled={busy === r.id}>Not for me</button>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Rewrite `RecommendationsView.css`**

```css
.view-head { display: flex; align-items: baseline; gap: 10px; }
.view-head__summary { font-size: var(--fs-sm); color: var(--text-muted); }
.rec-list { display: flex; flex-direction: column; gap: var(--space-3); }
.rec-card { display: flex; flex-direction: column; gap: var(--space-2); }
.rec-genre { position: absolute; top: 11px; right: 16px; color: var(--gilt); }
.rec-title { font-family: var(--font-display); font-weight: 500; font-size: var(--fs-title); color: var(--text); }
.rec-authors { color: var(--text-muted); font-size: var(--fs-sm); font-style: italic; }
.rec-why { color: var(--text-muted); font-size: var(--fs-sm); line-height: 1.5; }
.rec-actions { display: flex; gap: var(--space-2); }
.rec-badge { font-size: var(--fs-xs); padding: .1rem .4rem; border-radius: var(--radius); margin-left: .5rem; color: var(--on-badge); }
.rec-badge.new { background: var(--badge-new-bg); }
.rec-badge.reread { background: var(--badge-reread-bg); }
```

- [ ] **Step 4: Run the view's tests; fix queries if needed**

Run: `npx vitest run src/views/RecommendationsView.test.tsx`
Expected: PASS. (Button text "✓ I read this" / "Not for me" unchanged; classes added, not removed. If the test asserts an exact `className`, update it.)

- [ ] **Step 5: Commit**
```bash
git add src/views/RecommendationsView.tsx src/views/RecommendationsView.css src/api/client.ts
git commit -m "feat(ui): book-card recommendations with genre icon + new marker"
```

---

### Task 9: History + HistoryEdit views

**Files:**
- Modify: `frontend/src/views/HistoryView.css`, `frontend/src/views/HistoryView.tsx` (book-card + Literata titles + genre icon), `frontend/src/views/HistoryEditView.tsx` (token-based form)

- [ ] **Step 1:** Apply `.book-card` to history rows; set titles to `var(--font-display)` weight 500; muted author/date in tokens; render `<GenreIcon genres={item.genres} />` if the history item type carries genres (else omit). Buttons → `.btn`/`.btn--ghost`.
- [ ] **Step 2:** Restyle `HistoryView.css` using the same token set as Task 8 (`--surface`, `--text`, `--text-muted`, `--border`, leaf chips for any tags).
- [ ] **Step 3:** Run `npx vitest run src/views/HistoryView.test.tsx src/views/HistoryEditView.test.tsx`; keep assertions green (update class queries only).
- [ ] **Step 4:** `npm run dev`, verify History in light + dark.
- [ ] **Step 5:** Commit `git commit -m "feat(ui): restyle History + HistoryEdit onto Arcane Library"`.

---

### Task 10: Add-book + Analysis + Chat + ActivityTrail views

**Files:** `frontend/src/views/{AddBookView,AnalysisView,ChatView,ActivityTrail}.css` (+ minimal `.tsx` class swaps)

- [ ] **Step 1 (AddBook):** inputs/selects → `background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: var(--radius)`; focus ring via global `:focus-visible`; format pills → `.chip`; submit → `.btn`. Run `npx vitest run src/views/AddBookView.test.tsx`.
- [ ] **Step 2 (Analysis):** cards → `.book-card` or token surfaces; headings → `var(--font-display)`; any genre/trope chips → `.chip`/`.chip--special`; bars/accents → `var(--accent)`/`var(--gilt)`. Run its test.
- [ ] **Step 3 (Chat):** message bubbles — user: `background: var(--surface-2); color: var(--text)`; librarian: `background: var(--strong-bg); color: var(--strong-fg)`; any book titles in replies → `var(--font-display)`. Run its test.
- [ ] **Step 4 (ActivityTrail):** timeline marks → `var(--gilt)`/`var(--accent)`; text tokens; rules → `1px solid var(--border)`. Run its test.
- [ ] **Step 5:** `npm run dev` walk of all four in both themes; commit `git commit -m "feat(ui): restyle AddBook, Analysis, Chat, ActivityTrail"`.

---

### Task 11: Import view + SignIn/NotInvited

**Files:** `frontend/src/views/ImportView.css` (+ `.tsx` class swaps), `frontend/src/components/{SignIn,NotInvited}.tsx`

- [ ] **Step 1 (Import wizard):** map the wizard's surfaces/inputs/buttons/steps onto tokens and `.btn`/`.chip`/`.book-card` so it matches the identity (it currently hardcodes its own styles). Preserve all step text/roles the tests assert.
- [ ] **Step 2:** Run `npx vitest run src/views/ImportView.test.tsx src/api/client.import.test.ts`; keep green.
- [ ] **Step 3 (SignIn/NotInvited):** wrap content on `var(--bg)`, headings in `var(--font-display)`, primary action `.btn`, a leading ✦ on the title for brand consistency.
- [ ] **Step 4:** `npm run dev` (sign-out to see SignIn); commit `git commit -m "feat(ui): restyle Import wizard + SignIn/NotInvited"`.

---

## Phase 5 — Data hookup, a11y, verification

### Task 12 (optional, cross-scope): surface `genres` so icons use real data

> Without this, `GenreIcon` shows the fallback star (the component already degrades gracefully). This is the **one backend touch** — additive, read-only. Coordinate on `.git/AGENT_COORDINATION.md` first (the bench owner holds `src/agentic_librarian/**`).

**Files:** the recommendations API serializer in `src/agentic_librarian/api/` (where `Recommendation`s are built from `Works`), and `frontend/src/api/client.ts`.

- [ ] **Step 1:** In the API response model for recommendations, include `genres: work.genres or []`.
- [ ] **Step 2:** Confirm `Recommendation.genres?: string[]` (added in Task 8) matches the field name.
- [ ] **Step 3:** Add/extend a client test asserting `genres` round-trips (`src/api/client.test.ts`). Run `npm test`.
- [ ] **Step 4:** Backend: run `uvx ruff@0.15.16 format .` + the API test for the serializer; commit `git commit -m "feat(api): include genres in recommendations payload"`.

### Task 13: Accessibility + reduced-motion sweep

**Files:** `frontend/src/index.css` (already has `:focus-visible` + reduced-motion from Task 2 — verify), any view with custom focusable controls.

- [ ] **Step 1:** Audit contrast of the key pairs (`--text` on `--bg`/`--surface`, `--text-muted` on `--surface`, `--on-accent` on `--accent`, chip fg on chip bg) with a contrast checker; if any pair is < AA, deepen the muted/faint token in `index.css` and re-check.
- [ ] **Step 2:** Confirm every interactive element shows the gilt `:focus-visible` ring (tab through the app); add explicit rings where a control suppresses outline.
- [ ] **Step 3:** Toggle OS "reduce motion"; confirm theme transition + marker glow are disabled.
- [ ] **Step 4:** `npm test`; commit any token tweaks `git commit -m "fix(ui): a11y contrast + focus + reduced-motion"`.

### Task 14: Token smoke test + full verification

**Files:** Create `frontend/src/index.theme.test.ts`

- [ ] **Step 1: Write a token-presence smoke test**

```ts
import { describe, it, expect, beforeEach } from 'vitest'
import { applyTheme } from './theme'

const CORE = ['--bg', '--surface', '--text', '--accent', '--gilt', '--spine', '--page-edge', '--font-display']

describe('design tokens', () => {
  beforeEach(() => { document.head.insertAdjacentHTML('beforeend', '<style>@import "./index.css";</style>') })
  it.each(['light', 'dark'] as const)('core tokens are defined in %s', (theme) => {
    applyTheme(theme)
    const cs = getComputedStyle(document.documentElement)
    // jsdom doesn't load @import CSS; assert the contract instead by checking applyTheme set the attribute
    expect(document.documentElement.dataset.theme).toBe(theme)
    expect(CORE.length).toBeGreaterThan(0)
  })
})
```
> Note: jsdom does not evaluate `@import`ed CSS, so this test asserts `applyTheme` wiring, not computed values. Real token verification is the manual walk below — keep this test light.

- [ ] **Step 2: Run the whole suite**

Run: `npm test`
Expected: all green.

- [ ] **Step 3: Manual verification walk**

Run `npm run dev`. In **both** light and dark, confirm: parchment grain / starfield ground; leather binding + "✦ The Librarian"; nav active spine indicator; book-form recommendation cards (spine, page edge, leaf chips, genre icon, ✦ marker when an item is new); History/Add/Analysis/Chat/ActivityTrail/Import all on-identity; visible focus rings. Capture before/after screenshots of Recommendations (light + dark).

- [ ] **Step 4: Commit**
```bash
git add src/index.theme.test.ts
git commit -m "test(ui): design-token smoke test"
```

### Task 15: Open the PR

- [ ] **Step 1:** `npm run build && npm test` (green) and `npm run lint`.
- [ ] **Step 2:** Push branch; open PR "feat(ui): Visual Identity v2 — Arcane Library" linking the spec; attach light/dark screenshots.
- [ ] **Step 3:** Update `.git/AGENT_COORDINATION.md` design-work row → "PR open".

---

## Notes for the executor

- **CI gotcha (from the board):** only the Python side has the `ruff-format` trap; the frontend uses `eslint` + `vitest`. If Task 12 touches backend, run `uvx ruff@0.15.16 format .` before committing.
- **Don't fight existing tests:** restyles add/rename classes; when a `*.test.tsx` queries a class you renamed, update the query in the same task. Never weaken an assertion to make it pass.
- **`color-mix` fallback:** if the build target rejects `color-mix`, substitute the literal rgba values noted in Task 3.
- **Icon paths are law:** copy from spec Appendix A exactly; the user iterated these to final.
