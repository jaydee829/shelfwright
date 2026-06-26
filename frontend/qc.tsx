/* LOCAL VISUAL QC HARNESS (throwaway, git-excluded). Renders the real reskinned
 * AppShell + views with stubbed fetch fixtures — no backend, no Firebase, no auth.
 * Open http://localhost:5173/qc.html. Navigate via the nav; toggle theme in the top bar.
 * NOTE: this is NOT shipped — it only exists in this worktree for eyeballing the reskin. */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router'
import '@fontsource-variable/inter'
import '@fontsource-variable/literata'
import { applyTheme, resolveInitialTheme } from './src/theme'
import './src/index.css'
import { AuthProvider } from './src/auth/AuthContext'
import AppShell from './src/components/AppShell'
import AddBookView from './src/views/AddBookView'
import AnalysisView from './src/views/AnalysisView'
import ChatView from './src/views/ChatView'
import HistoryEditView from './src/views/HistoryEditView'
import HistoryView from './src/views/HistoryView'
import ImportView from './src/views/ImportView'
import RecommendationsView from './src/views/RecommendationsView'

// ---- fixtures ----
const recommendations = [
  {
    id: 'r1', work_id: 'w1', title: 'The Fifth Season', authors: ['N. K. Jemisin'],
    justification: 'Because you loved character-driven epics with a melancholic narrator and earned, hard-won hope.',
    context: null, suggested_at: '2026-06-20T10:00:00Z', status: 'Suggested',
    read_status: 'new', last_read: null, rating: null, genres: ['science-fiction-fantasy', 'dystopian'],
  },
  {
    id: 'r2', work_id: 'w2', title: 'A Memory Called Empire', authors: ['Arkady Martine'],
    justification: 'Political intrigue and questions of identity, in the vein of your recent space-opera reads.',
    context: null, suggested_at: '2026-06-19T10:00:00Z', status: 'Suggested',
    read_status: 'new', last_read: null, rating: null, genres: ['science-fiction', 'space-opera'],
  },
  {
    id: 'r3', work_id: 'w3', title: 'The Goblin Emperor', authors: ['Katherine Addison'],
    justification: 'A gentle court-fantasy about kindness as a kind of power.',
    context: null, suggested_at: '2026-06-18T10:00:00Z', status: 'Suggested',
    read_status: 'reread', last_read: '2021-04-02', rating: 5, genres: ['fantasy'],
  },
  {
    id: 'r4', work_id: 'w4', title: 'The Name of the Wind', authors: ['Patrick Rothfuss'],
    justification: 'Lyrical prose and a frame-story structure you tend to rate highly.',
    context: null, suggested_at: '2026-06-17T10:00:00Z', status: 'Suggested',
    read_status: 'new', last_read: null, rating: null, genres: ['high-fantasy'],
  },
]

const history = [
  { id: 'h1', title: 'Piranesi', authors: ['Susanna Clarke'], date_completed: '2026-06-10', rating: 5, format: 'hardcover', notes: 'Luminous.', genre: 'fantasy', tropes: ['unreliable narrator', 'liminal spaces', 'isolation'] },
  { id: 'h2', title: 'Project Hail Mary', authors: ['Andy Weir'], date_completed: '2026-05-22', rating: 4, format: 'audiobook', notes: null, genre: 'science-fiction', tropes: ['first contact', 'problem solving'] },
  { id: 'h3', title: 'The Spear Cuts Through Water', authors: ['Simon Jimenez'], date_completed: '2026-05-01', rating: 5, format: 'ebook', notes: null, genre: 'fantasy', tropes: ['mythic', 'second person'] },
  { id: 'h4', title: 'Babel', authors: ['R. F. Kuang'], date_completed: '2026-04-12', rating: 4, format: 'hardcover', notes: null, genre: 'historical', tropes: [] },
]

const analysis = {
  snapshot: { total_read: 142, read_this_year: 31, average_rating: 4.2, distinct_authors: 96, formats: [{ name: 'audiobook', count: 71 }, { name: 'hardcover', count: 38 }, { name: 'ebook', count: 33 }] },
  genres: [{ name: 'fantasy', count: 58 }, { name: 'science fiction', count: 47 }, { name: 'literary', count: 18 }, { name: 'mystery', count: 12 }],
  moods: [{ name: 'reflective', count: 40 }, { name: 'adventurous', count: 33 }, { name: 'dark', count: 22 }],
  top_tropes: [
    { name: 'Found Family', count: 29 }, { name: 'Enemies / Lovers', count: 24 },
    { name: 'Morally Grey', count: 21 }, { name: 'The Chosen One', count: 18 },
    { name: 'Slow Burn', count: 17 }, { name: 'Hidden Identity', count: 14 },
    { name: 'Quest', count: 12 }, { name: 'Mentor / Protege', count: 10 },
    { name: 'Forbidden Love', count: 9 }, { name: 'A Court Intrigue', count: 7 },
    { name: 'Redemption Arc', count: 6 }, { name: 'Found Family', count: 5 },
    { name: 'Unreliable Narrator', count: 4 }, { name: 'Time Loop', count: 3 },
    { name: 'Heist', count: 2 },
  ],
  authors: [{ name: 'N. K. Jemisin', count: 6 }, { name: 'Ursula K. Le Guin', count: 5 }],
  narrators: [{ name: 'Ray Porter', count: 8 }, { name: 'Moira Quirk', count: 4 }],
  style_radar: { pace: 0.74, density: 0.42, depth: 0.66, inner_focus: 0.55, humor: 0.21, warmth: 0.7, lexicon: 0.5, world_building: 0.82 },
  style_cloud: [
    { name: 'Atmospheric', count: 22 }, { name: 'Lyrical', count: 14 },
    { name: 'First / Third Person', count: 12 }, { name: 'Cynical', count: 9 },
    { name: 'Minimalist', count: 7 }, { name: 'Unreliable', count: 5 },
    { name: 'Wry', count: 4 }, { name: 'Naturalistic', count: 3 },
    { name: 'Ornate', count: 2 },
  ],
}

const conversation = {
  id: 'c1',
  messages: [
    { role: 'user', content: 'I want something hopeful but not saccharine after a run of bleak sci-fi.' },
    { role: 'assistant', content: 'Try **The Goblin Emperor** — a court fantasy where decency is the quiet superpower. If you want to stay in SF, **A Psalm for the Wild-Built** is gentle and searching without being sentimental.' },
  ],
}

// ---- fetch stub ----
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })

const realFetch = window.fetch.bind(window)
window.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const path = new URL(url, location.origin).pathname
  // let font/asset/HMR requests pass through to the real dev server
  if (!path.match(/^\/(recommendations|history|analysis|conversations|chat|books)/)) return realFetch(input as RequestInfo, init)
  if (path === '/recommendations') return Promise.resolve(json(recommendations))
  if (path === '/history') return Promise.resolve(json(history))
  if (path === '/analysis') return Promise.resolve(json(analysis))
  if (path === '/conversations/current') return Promise.resolve(json(conversation))
  // POST mutations (dismiss / add / new conversation) — benign OK so the UI doesn't error
  return Promise.resolve(json({ ok: true }))
}

applyTheme(resolveInitialTheme())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <MemoryRouter initialEntries={['/recommendations']}>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<ChatView />} />
            <Route path="history" element={<HistoryView />} />
            <Route path="history/:id/edit" element={<HistoryEditView />} />
            <Route path="recommendations" element={<RecommendationsView />} />
            <Route path="analysis" element={<AnalysisView />} />
            <Route path="add" element={<AddBookView />} />
            <Route path="import" element={<ImportView />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AuthProvider>
  </StrictMode>,
)
