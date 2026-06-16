import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import HistoryView from './HistoryView'
import * as client from '../api/client'

// The auto-mock of '../api/client' imports the real module to derive its shape,
// which transitively loads firebase (getAuth throws without env keys under test).
vi.mock('../auth/firebase', () => ({ getIdToken: vi.fn().mockResolvedValue(null) }))
vi.mock('../api/client')

function item(id: string, title: string): client.HistoryItem {
  return { id, title, authors: ['A. Uthor'], date_completed: '2024-01-01', rating: 4, format: 'ebook' }
}

afterEach(() => vi.clearAllMocks())

describe('HistoryView pagination', () => {
  it('loads the first page and appends the next on "Load more"', async () => {
    const full = Array.from({ length: 50 }, (_, i) => item(`a${i}`, `Book ${i}`))
    vi.mocked(client.getHistory).mockResolvedValueOnce(full).mockResolvedValueOnce([item('b0', 'Page 2 Book')])

    render(<HistoryView />)
    expect(await screen.findByText('Book 0')).toBeInTheDocument()
    expect(client.getHistory).toHaveBeenCalledWith(50, 0)

    await userEvent.click(screen.getByRole('button', { name: /load more/i }))
    expect(await screen.findByText('Page 2 Book')).toBeInTheDocument()
    expect(client.getHistory).toHaveBeenCalledWith(50, 50)
  })

  it('hides "Load more" when the first page is short (no more rows)', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Only Book')])
    render(<HistoryView />)
    expect(await screen.findByText('Only Book')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })

  it('renders genre + trope chips when a row has tropes', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([
      {
        id: 'x', title: 'Tropey', authors: ['A'], date_completed: '2024-01-01', rating: 4, format: 'ebook',
        genre: 'Fantasy', tropes: ['Found Family', 'Antihero', 'Heist'],
      },
    ])
    render(<HistoryView />)
    expect(await screen.findByText('Tropey')).toBeInTheDocument()
    expect(screen.getByText('Fantasy')).toBeInTheDocument()
    expect(screen.getByText('Found Family')).toBeInTheDocument()
    expect(screen.queryByText(/Enriching/)).not.toBeInTheDocument()
  })

  it('renders an Enriching… chip when a row has no tropes', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([
      {
        id: 'y', title: 'Fresh', authors: ['B'], date_completed: '2024-01-01', rating: null, format: 'ebook',
        genre: null, tropes: [],
      },
    ])
    render(<HistoryView />)
    expect(await screen.findByText('Fresh')).toBeInTheDocument()
    expect(screen.getByText(/Enriching/)).toBeInTheDocument()
  })
})
