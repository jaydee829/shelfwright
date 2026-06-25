import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Analysis } from '../api/client'

vi.mock('../api/client', () => ({ getAnalysis: vi.fn() }))

import { getAnalysis } from '../api/client'
import AnalysisView from './AnalysisView'

const base: Analysis = {
  snapshot: {
    total_read: 12, read_this_year: 4, average_rating: 4.2, distinct_authors: 9,
    formats: [{ name: 'Audiobook', count: 10 }, { name: 'Ebook', count: 2 }],
  },
  genres: [{ name: 'Sci-Fi', count: 6 }],
  moods: [{ name: 'Epic', count: 5 }],
  top_tropes: [{ name: 'Chosen One', count: 3 }],
  authors: [{ name: 'Herbert', count: 2 }],
  narrators: [{ name: 'Vance', count: 4 }],
  style_radar: {
    pace: 0.7, density: 0.4, depth: 0.6, inner_focus: 0.5,
    humor: 0.2, warmth: 0.7, lexicon: 0.5, world_building: 0.8,
  },
  style_cloud: [{ name: 'Atmospheric', count: 7 }, { name: 'Lyrical', count: 3 }],
}

describe('AnalysisView', () => {
  beforeEach(() => vi.mocked(getAnalysis).mockResolvedValue(base))
  afterEach(() => vi.clearAllMocks())

  it('renders the snapshot, tropes, style, and people in one scroll', async () => {
    render(<AnalysisView />)
    expect(await screen.findByText('12')).toBeInTheDocument()
    expect(screen.getByText('Chosen One')).toBeInTheDocument()
    expect(screen.getByText('Atmospheric')).toBeInTheDocument()
    expect(screen.getByText('Vance')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /shape of your reading/i })).toBeInTheDocument()
  })

  it('degrades gracefully when style fields are absent', async () => {
    vi.mocked(getAnalysis).mockResolvedValueOnce({ ...base, style_radar: undefined, style_cloud: undefined })
    render(<AnalysisView />)
    expect(await screen.findByText('12')).toBeInTheDocument()
    expect(screen.getByText(/gathering your style/i)).toBeInTheDocument()
  })
})
