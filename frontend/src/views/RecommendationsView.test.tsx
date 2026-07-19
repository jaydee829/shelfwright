import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  getRecommendations: vi.fn(),
  setRecommendationStatus: vi.fn(),
  getAvailability: vi.fn(),
}))

import { getRecommendations, setRecommendationStatus, getAvailability } from '../api/client'
import RecommendationsView from './RecommendationsView'

const rec = {
  id: 'r1', work_id: 'w1', title: 'Project Hail Mary', authors: ['Weir'],
  justification: 'You loved The Martian', context: null,
  suggested_at: '2026-06-01T00:00:00', status: 'Suggested',
}

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="loc">{loc.pathname}|{JSON.stringify(loc.state)}</div>
}

function renderWithRouter() {
  return render(
    <MemoryRouter initialEntries={['/recommendations']}>
      <Routes>
        <Route path="/recommendations" element={<RecommendationsView />} />
        <Route path="/add" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('RecommendationsView', () => {
  beforeEach(() => {
    vi.mocked(getRecommendations).mockResolvedValue([rec])
    vi.mocked(setRecommendationStatus).mockResolvedValue()
    vi.mocked(getAvailability).mockResolvedValue({})
  })
  afterEach(() => vi.clearAllMocks())

  it('renders recommendation cards with the justification', async () => {
    renderWithRouter()
    expect(await screen.findByText('Project Hail Mary')).toBeInTheDocument()
    expect(screen.getByText(/You loved The Martian/)).toBeInTheDocument()
  })

  it('dismisses a recommendation and removes the card', async () => {
    renderWithRouter()
    await screen.findByText('Project Hail Mary')
    await userEvent.click(screen.getByRole('button', { name: /not for me/i }))
    expect(vi.mocked(setRecommendationStatus)).toHaveBeenCalledWith('r1', 'Dismissed')
    await waitFor(() => expect(screen.queryByText('Project Hail Mary')).not.toBeInTheDocument())
  })

  it('"I read this" navigates to /add prefilled with the title, author, and suggestion id', async () => {
    renderWithRouter()
    await screen.findByText('Project Hail Mary')
    await userEvent.click(screen.getByRole('button', { name: /i read this/i }))
    const probe = await screen.findByTestId('loc')
    expect(probe.textContent).toContain('/add')
    expect(probe.textContent).toContain('"title":"Project Hail Mary"')
    expect(probe.textContent).toContain('"author":"Weir"')
    expect(probe.textContent).toContain('"suggestionId":"r1"')
  })

  it('shows an empty state when there are no picks', async () => {
    vi.mocked(getRecommendations).mockResolvedValue([])
    renderWithRouter()
    expect(await screen.findByText(/no recommendations/i)).toBeInTheDocument()
  })

  it('renders a New badge for unread recs and a Re-read badge for read ones', async () => {
    vi.mocked(getRecommendations).mockResolvedValueOnce([
      { id: '1', work_id: 'w1', title: 'Fresh Pick', authors: ['A'], justification: null,
        context: null, suggested_at: null, status: 'Suggested', read_status: 'new', last_read: null, rating: null },
      { id: '2', work_id: 'w2', title: 'Old Favorite', authors: ['B'], justification: null,
        context: null, suggested_at: null, status: 'Suggested', read_status: 'reread', last_read: '2019-05-01', rating: 4 },
    ])
    renderWithRouter()

    // 'new' (not-a-reread) recs no longer show a read-status badge — only 'reread' does.
    await screen.findByText(/Re-read/)
    expect(document.querySelector('.rec-badge.new')).toBeNull()
    expect(screen.getByText(/Re-read/)).toBeInTheDocument()
    expect(screen.getByText(/2019/)).toBeInTheDocument()
  })

  it('keeps "New" markers on the remaining recs after dismissing one (no mass-clear)', async () => {
    localStorage.clear()
    vi.mocked(getRecommendations).mockResolvedValueOnce([
      { id: 'a', work_id: 'wa', title: 'Alpha', authors: ['A'], justification: null, context: null, suggested_at: null, status: 'Suggested' },
      { id: 'b', work_id: 'wb', title: 'Beta', authors: ['B'], justification: null, context: null, suggested_at: null, status: 'Suggested' },
    ])
    renderWithRouter()
    await screen.findByText('Alpha')
    // both are new (localStorage was empty), so two markers + a "2 new" header
    expect(screen.getAllByText('New')).toHaveLength(2)
    expect(screen.getByText('2 new')).toBeInTheDocument()

    const alphaCard = screen.getByText('Alpha').closest('article')!
    await userEvent.click(within(alphaCard).getByRole('button', { name: /not for me/i }))
    await waitFor(() => expect(screen.queryByText('Alpha')).not.toBeInTheDocument())

    // Beta's marker must persist and the count decrement to 1 — it must NOT mass-clear to 0.
    expect(screen.getAllByText('New')).toHaveLength(1)
    expect(screen.getByText('1 new')).toBeInTheDocument()
  })

  it('"Not right now" removes the card with the neutral Removed status', async () => {
    renderWithRouter()
    await screen.findByText('Project Hail Mary')
    await userEvent.click(screen.getByRole('button', { name: /not right now/i }))
    expect(vi.mocked(setRecommendationStatus)).toHaveBeenCalledWith('r1', 'Removed')
    await waitFor(() => expect(screen.queryByText('Project Hail Mary')).not.toBeInTheDocument())
  })
})
