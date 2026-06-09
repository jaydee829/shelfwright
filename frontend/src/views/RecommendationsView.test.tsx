import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  getRecommendations: vi.fn(),
  setRecommendationStatus: vi.fn(),
}))

import { getRecommendations, setRecommendationStatus } from '../api/client'
import RecommendationsView from './RecommendationsView'

const rec = {
  id: 'r1',
  work_id: 'w1',
  title: 'Project Hail Mary',
  authors: ['Weir'],
  justification: 'You loved The Martian',
  context: null,
  suggested_at: '2026-06-01T00:00:00',
  status: 'Suggested',
}

describe('RecommendationsView', () => {
  beforeEach(() => {
    vi.mocked(getRecommendations).mockResolvedValue([rec])
    vi.mocked(setRecommendationStatus).mockResolvedValue()
  })
  afterEach(() => vi.clearAllMocks())

  it('renders recommendation cards with the justification', async () => {
    render(<RecommendationsView />)
    expect(await screen.findByText('Project Hail Mary')).toBeInTheDocument()
    expect(screen.getByText(/You loved The Martian/)).toBeInTheDocument()
  })

  it('dismisses a recommendation and removes the card', async () => {
    render(<RecommendationsView />)
    await screen.findByText('Project Hail Mary')
    await userEvent.click(screen.getByRole('button', { name: /not for me/i }))
    expect(vi.mocked(setRecommendationStatus)).toHaveBeenCalledWith('r1', 'Dismissed')
    await waitFor(() => expect(screen.queryByText('Project Hail Mary')).not.toBeInTheDocument())
  })

  it('shows "I read this" as disabled (Stage 3)', async () => {
    render(<RecommendationsView />)
    await screen.findByText('Project Hail Mary')
    expect(screen.getByRole('button', { name: /i read this/i })).toBeDisabled()
  })

  it('shows an empty state when there are no picks', async () => {
    vi.mocked(getRecommendations).mockResolvedValue([])
    render(<RecommendationsView />)
    expect(await screen.findByText(/no recommendations/i)).toBeInTheDocument()
  })
})
