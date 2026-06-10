import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Analysis } from '../api/client'

vi.mock('../api/client', () => ({ getAnalysis: vi.fn() }))

import { getAnalysis } from '../api/client'
import AnalysisView from './AnalysisView'

const analysis: Analysis = {
  snapshot: {
    total_read: 12,
    read_this_year: 4,
    average_rating: 4.2,
    distinct_authors: 9,
    formats: [{ name: 'audiobook', count: 10 }, { name: 'ebook', count: 2 }],
  },
  genres: [{ name: 'Sci-Fi', count: 6 }],
  moods: [{ name: 'epic', count: 5 }],
  top_tropes: [{ name: 'chosen one', count: 3 }],
  authors: [{ name: 'Herbert', count: 2 }],
  narrators: [{ name: 'Vance', count: 4 }],
}

describe('AnalysisView', () => {
  beforeEach(() => vi.mocked(getAnalysis).mockResolvedValue(analysis))
  afterEach(() => vi.clearAllMocks())

  it('shows the snapshot numbers by default', async () => {
    render(<AnalysisView />)
    expect(await screen.findByText('12')).toBeInTheDocument() // total read
    expect(screen.getByText('4.2')).toBeInTheDocument() // average rating
  })

  it('switches to the Top tropes tab', async () => {
    render(<AnalysisView />)
    await screen.findByText('12')
    await userEvent.click(screen.getByRole('tab', { name: /top tropes/i }))
    await waitFor(() => expect(screen.getByText('chosen one')).toBeInTheDocument())
  })

  it('switches to the Authors & narrators tab', async () => {
    render(<AnalysisView />)
    await screen.findByText('12')
    await userEvent.click(screen.getByRole('tab', { name: /authors/i }))
    await waitFor(() => expect(screen.getByText('Vance')).toBeInTheDocument())
  })
})
