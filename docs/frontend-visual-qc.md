# Frontend Visual QC Harness

A **dev-only** way to eyeball the real frontend views with realistic data — **no backend, no
Firebase, no database** — and to capture headless screenshots (which an AI agent can read back).
Built during the "Visual Identity v2" work; reuse it for any future design/visual change.

> Dev tooling only. `qc.html` is **not** a production build entry (Vite builds `index.html`), and
> the dummy `.env.local` is gitignored. Nothing here ships.

## What's in the repo

- **`frontend/qc.html`** — a standalone Vite entry (parallel to `index.html`).
- **`frontend/qc.tsx`** — the harness: it stubs `window.fetch` with fixture data, then renders the
  **real** `AppShell` + every view inside a `MemoryRouter`. Because `fetch` is stubbed, the real API
  client just reads the fixtures; no auth/token is needed. Update the fixtures at the top of this
  file to exercise different states (genres, read-status, tropes, ratings, chat messages, …).

## 1. One-time setup

The dev server needs a `.env.local` with the `VITE_FIREBASE_*` keys (otherwise `firebase.ts`'s
`getAuth()` throws and the page is blank). The harness stubs `fetch`, so **dummy values are fine** —
you do not need a real Firebase project:

```bash
# frontend/.env.local  (gitignored)
VITE_FIREBASE_API_KEY=AIzaSyDUMMY-local-qc-not-a-real-key-000000
VITE_FIREBASE_AUTH_DOMAIN=demo-local.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=demo-local
VITE_FIREBASE_APP_ID=1:000000000000:web:0000000000000000000000
```

## 2. Look at it in your browser

```bash
cd frontend
npm run dev
# open http://localhost:5173/qc.html
```

It opens on **Recommendations**. Use the nav to move between views and the 🌙/☀ button (top bar) to
toggle light/dark. The "New" markers show on first load and clear on reload (real `localStorage`
behaviour) — open in a private window to see them again.

## 3. Headless screenshots (for an agent, or batch capture)

This is what lets an AI agent close the loop: drive the harness with Playwright, screenshot every
view in both themes, and read the PNGs.

```bash
cd frontend
npm i -D playwright          # not a committed dep — install on demand
npx playwright install chromium
node qc-shot.mjs             # create this file from the block below
```

`frontend/qc-shot.mjs` (kept out of git so it never touches `tsc`/CI; recreate as needed):

```js
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const URL = 'http://localhost:5173/qc.html'
const OUT = process.env.OUT || 'C:/Users/<you>/AppData/Local/Temp/qc-shots'
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
// width >= 768 so the desktop nav rail (not the mobile bottom bar) is used
const page = await browser.newPage({ viewport: { width: 900, height: 1300 }, deviceScaleFactor: 2 })
const errors = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message))

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForSelector('.rec-card', { timeout: 15000 })

const nav = (label) => page.locator('.nav .nav-item', { hasText: label }).click()
const shoot = async (name) => { await page.waitForTimeout(350); await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true }) }
async function setTheme(t) {
  const cur = await page.evaluate(() => document.documentElement.dataset.theme)
  if (cur !== t) { await page.locator('.theme-toggle').click(); await page.waitForTimeout(250) }
}

const views = [
  { name: 'recommendations', go: () => nav('Picks'), wait: '.rec-card' },
  { name: 'history', go: () => nav('History'), wait: '.history-row' },
  { name: 'analysis', go: () => nav('Analysis'), wait: 'h2' },
  { name: 'chat', go: () => nav('Chat'), wait: 'h2' },
  { name: 'add', go: () => nav('Add'), wait: 'form' },
]
for (const v of views) {
  await v.go()
  await page.waitForSelector(v.wait, { timeout: 15000 }).catch(() => {})
  await setTheme('light'); await shoot(`${v.name}-light`)
  await setTheme('dark'); await shoot(`${v.name}-dark`)
  await setTheme('light')
}
console.log('CONSOLE_ERRORS', JSON.stringify(errors))
await browser.close()
```

### Inspect computed styles (debugging a CSS issue)

```js
const info = await page.locator('.history-tropes .chip').first().evaluate((n) => {
  const cs = getComputedStyle(n)
  return { cls: n.className, background: cs.backgroundColor, boxShadow: cs.boxShadow, borderRadius: cs.borderRadius }
})
console.log(info)
```

### Quick A/B of a colour without editing source

Inject a stylesheet override at runtime, screenshot, repeat — no commits:

```js
await page.addStyleTag({ content: `.history-tropes .chip:not(.history-genre){ background:#e1c2ae!important; color:#6e271d!important }` })
await page.locator('.history-list').screenshot({ path: `${OUT}/variant.png` })
```

## Gotchas

- **Blank page** → missing/empty `.env.local` (Firebase `getAuth()` throws). Add the dummy values above.
- **`@import`ed CSS doesn't hot-reload** → editing `index.css` tokens or `styles/primitives.css` may
  need a hard reload (Ctrl+Shift+R); a fresh Playwright load always reflects the latest.
- **Nav click intercepted** → use viewport width ≥ 768 (below that the nav is a fixed bottom bar).
- **Stale fixtures** → if a view's data shape changes, update the fixtures in `qc.tsx`.
