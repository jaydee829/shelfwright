import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  getRecommendations: vi.fn(),
  setRecommendationStatus: vi.fn(),
}))

import { getRecommendations, setRecommendationStatus } from '../api/client'
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
})
