# Dark Mode — Design (E1)

**Date:** 2026-06-17
**Status:** Approved (brainstormed), pending plan
**Origin:** Friends-and-family beta feedback, item E1 (see project memory `beta-feedback-triage`). Last
beta item before the batched prod redeploy.

---

## 1. Background & Problem

The operator wants a dark mode. Today the frontend has **no theming layer**: `index.css` hardcodes
`color: #1a1a1a`, and there are ~46 hardcoded hex colors across 9 component/view CSS files. There is no
`:root` variable set, no `prefers-color-scheme` handling, and no `data-theme` mechanism.
(`AddBookView.css` references `var(--ok)` / `var(--danger)` with literal fallbacks, but those variables
are never actually defined.)

So dark mode requires two things: (1) **tokenize** the hardcoded colors into semantic CSS variables, and
(2) a **toggle** that switches between a light and a dark token set.

**Forward note:** this token layer is the deliberate foundation for the future "Visual Identity v2"
initiative (see `beta-feedback-triage`) — that work will *extend* these tokens (palette + type/spacing
scales + component restyle), so E1 is groundwork, not throwaway.

## 2. Goals

- A working dark theme across the whole app.
- A TopBar toggle that defaults to the OS preference on first visit and persists the user's choice.
- **Light mode looks identical to today** (the token refactor is visually a no-op in light).

## 3. Non-Goals (YAGNI)

- No visual redesign / new personality — that's the separate "Visual Identity v2" initiative.
- No type scale, spacing scale, or component restyling beyond color tokens (those come with v2).
- No per-component theme overrides or multiple themes — just light + dark.
- No backend changes; no persistence beyond `localStorage`.

## 4. Decisions (from brainstorm)

- **D1 — Control:** a manual toggle that **defaults to the OS preference** (`prefers-color-scheme`) on
  first visit, then persists the user's explicit choice to `localStorage` (which wins thereafter).
- **D2 — Toggle location/form:** a **☀/🌙 icon button** in the `TopBar` (next to the avatar / Sign out).
- **D3 — Mechanism:** `data-theme="light"|"dark"` on `document.documentElement`; dark values under
  `:root[data-theme="dark"]`.
- **D4 — Light tokens mirror current colors exactly** — light mode is a pure refactor, no visual change.
- **D5 — No flash of wrong theme:** resolve + set `data-theme` synchronously **before** React renders.

## 5. Architecture (frontend-only)

### 5.1 Token layer (`frontend/src/index.css`)

Define a semantic token set under `:root` (light = today's colors) and a dark override under
`:root[data-theme="dark"]`. Then replace the hardcoded hex across the 9 CSS files with `var(--token)`.
Proposed tokens (light → dark):

| token | role | light | dark |
|---|---|---|---|
| `--bg` | page background | `#ffffff` | `#0f1115` |
| `--surface` | cards / menus / assistant bubble | `#f3f4f6` | `#1b1f27` |
| `--text` | body text | `#1a1a1a` | `#e5e7eb` |
| `--text-muted` | meta / labels / placeholders | `#6b7280` | `#9ca3af` |
| `--border` | borders / inputs | `#d1d5db` | `#374151` |
| `--accent` | primary button / user bubble | `#6366f1` | `#818cf8` |
| `--accent-contrast` | text on accent | `#ffffff` | `#0f1115` |
| `--danger` | delete / errors | `#c62828` | `#f87171` |
| `--ok` | success | `#2e7d32` | `#66bb6a` |
| `--chip-bg` / `--chip-fg` | trope chips | `#eef2ff` / `#3730a3` | `#272e3f` / `#c7d2fe` |
| `--chip-genre-bg` | genre chip | `#e0e7ff` | `#313a52` |
| `--overlay` | dialog backdrop | `rgba(0,0,0,0.4)` | `rgba(0,0,0,0.6)` |
| `--menu-shadow` | menu/dialog shadow | `rgba(0,0,0,0.1)` | `rgba(0,0,0,0.5)` |

- `body` gets `background: var(--bg); color: var(--text);` (today `body` has only `color`).
- Every existing hex in the 9 CSS files maps to one of these tokens. Where a literal has no clean token
  (rare), add a token rather than leaving a hardcoded color — the point is zero hardcoded colors remain
  in component CSS after this pass.
- A short transition (`body { transition: background-color .15s, color .15s; }`) so the flip isn't jarring.

### 5.2 Theme module (`frontend/src/theme.ts`, new)

```
type Theme = 'light' | 'dark'
function resolveInitialTheme(): Theme   // localStorage['theme'] if valid, else matchMedia OS, else 'light'
function applyTheme(t: Theme): void      // document.documentElement.dataset.theme = t
function setTheme(t: Theme): void        // applyTheme + persist to localStorage (try/catch)
function getStoredTheme(): Theme | null  // null if absent/invalid/unavailable
```
- All `localStorage` access is wrapped in `try/catch` (private mode / disabled storage → fall back to OS
  / in-memory; never throw).
- `matchMedia('(prefers-color-scheme: dark)')` guarded for environments without `matchMedia`.

### 5.3 Flash guard (`frontend/src/main.tsx`)

Call `applyTheme(resolveInitialTheme())` **once, synchronously, before** `createRoot(...).render(...)`,
so `data-theme` is set before first paint (no flash of the wrong theme).

### 5.4 TopBar toggle (`frontend/src/components/TopBar.tsx` + `.css`)

- Local state initialized from `document.documentElement.dataset.theme` (already set by the flash guard).
- A button showing 🌙 in light mode (click → dark) and ☀ in dark mode (click → light); `onClick` calls
  `setTheme(next)` and updates local state. `aria-label` reflects the action ("Switch to dark mode").

## 6. Data Flow

```
main.tsx (before render): applyTheme(resolveInitialTheme())  // localStorage else OS -> data-theme on <html>
React renders; CSS reads var(--token); :root[data-theme="dark"] overrides apply in dark
TopBar toggle click -> setTheme(next) -> data-theme flips + localStorage persists -> CSS re-resolves
```

## 7. Error / Edge Handling

- `localStorage` throws (private mode) → `getStoredTheme` returns null, `setTheme` swallows the write
  error; theme still applies in-memory for the session.
- No `matchMedia` (old/headless env) → default `'light'`.
- Invalid stored value (not 'light'/'dark') → treated as absent → OS fallback.

## 8. Testing Strategy (vitest)

- `theme.ts`: `resolveInitialTheme` returns the stored value when valid; falls back to `matchMedia` when
  absent (mock `window.matchMedia`); defaults to `'light'` when neither; `setTheme` sets
  `document.documentElement.dataset.theme` AND writes `localStorage`; a throwing `localStorage` doesn't
  crash `setTheme`/`getStoredTheme`.
- `TopBar`: renders the toggle; clicking it flips `document.documentElement` `data-theme` and the button's
  `aria-label`/icon. (TopBar uses `useAuth`; the existing tests mock it — follow that pattern.)
- Existing suite stays green: light tokens equal the prior colors, so no visual/test regressions.
- `npm run build` (type-check) + `npm run lint` clean.

## 9. Files Touched

- `frontend/src/index.css` — token `:root` (light) + `:root[data-theme="dark"]` + `body` bg/color + transition.
- `frontend/src/theme.ts` (new) + `frontend/src/theme.test.ts` (new).
- `frontend/src/main.tsx` — flash guard.
- `frontend/src/components/TopBar.tsx` (+ `TopBar` CSS — likely in `AppShell.css`/`Nav.css`; verify where
  `.topbar` is styled) — toggle button + `TopBar.test.tsx` (new).
- The 8 other CSS files — replace hardcoded hex with `var(--token)`:
  `AppShell.css`, `Nav.css`, `ChatView.css`, `ActivityTrail.css`, `HistoryView.css`, `AddBookView.css`,
  `RecommendationsView.css`, `AnalysisView.css`.

## 10. Out of Scope / Future

- **Visual Identity v2** — the real personality/redesign (palette, typography, spacing, components,
  library-flavored voice) extends this token layer; rendered mockups via the frontend-design skill.
- A "system" third option (auto-follow OS even after a manual choice) — not needed; OS is just the default.
