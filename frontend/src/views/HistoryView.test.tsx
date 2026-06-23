import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'
import HistoryView from './HistoryView'
import * as client from '../api/client'

// The auto-mock of '../api/client' imports the real module to derive its shape,
// which transitively loads firebase (getAuth throws without env keys under test).
vi.mock('../auth/firebase', () => ({ getIdToken: vi.fn().mockResolvedValue(null) }))
vi.mock('../api/client')

function item(id: string, title: string): client.HistoryItem {
  return { id, title, authors: ['A. Uthor'], date_completed: '2024-01-01', rating: 4, format: 'ebook' }
}

const renderView = () => render(<HistoryView />, { wrapper: MemoryRouter })

afterEach(() => vi.clearAllMocks())

describe('HistoryView pagination', () => {
  it('loads the first page and appends the next on "Load more"', async () => {
    const full = Array.from({ length: 50 }, (_, i) => item(`a${i}`, `Book ${i}`))
    vi.mocked(client.getHistory).mockResolvedValueOnce(full).mockResolvedValueOnce([item('b0', 'Page 2 Book')])

    renderView()
    expect(await screen.findByText('Book 0')).toBeInTheDocument()
    expect(client.getHistory).toHaveBeenCalledWith(50, 0)

    await userEvent.click(screen.getByRole('button', { name: /load more/i }))
    expect(await screen.findByText('Page 2 Book')).toBeInTheDocument()
    expect(client.getHistory).toHaveBeenCalledWith(50, 50)
  })

  it('hides "Load more" when the first page is short (no more rows)', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Only Book')])
    renderView()
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
    renderView()
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
    renderView()
    expect(await screen.findByText('Fresh')).toBeInTheDocument()
    expect(screen.getByText(/Enriching/)).toBeInTheDocument()
  })

  it('opens the ⋮ menu with Edit and Delete', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Jhereg')])
    renderView()
    await screen.findByText('Jhereg')
    await userEvent.click(screen.getByRole('button', { name: /actions for Jhereg/i }))
    expect(screen.getByRole('button', { name: /^edit$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^delete$/i })).toBeInTheDocument()
  })

  it('confirms then deletes the row', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Jhereg')])
    vi.mocked(client.deleteHistory).mockResolvedValueOnce()
    renderView()
    await screen.findByText('Jhereg')
    await userEvent.click(screen.getByRole('button', { name: /actions for Jhereg/i }))
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }))
    await userEvent.click(screen.getByRole('button', { name: /delete entry/i }))
    expect(client.deleteHistory).toHaveBeenCalledWith('a0')
    await waitFor(() => expect(screen.queryByText('Jhereg')).not.toBeInTheDocument())
  })

  it('shows an error and keeps the row when delete fails', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Jhereg')])
    vi.mocked(client.deleteHistory).mockRejectedValueOnce(new Error('boom'))
    renderView()
    await screen.findByText('Jhereg')
    await userEvent.click(screen.getByRole('button', { name: /actions for Jhereg/i }))
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }))
    await userEvent.click(screen.getByRole('button', { name: /delete entry/i }))
    expect(await screen.findByText(/couldn't delete that entry/i)).toBeInTheDocument()
    expect(screen.getByText('Jhereg')).toBeInTheDocument()  // row stays
  })

  it('cancel in the confirm dialog keeps the row', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Jhereg')])
    renderView()
    await screen.findByText('Jhereg')
    await userEvent.click(screen.getByRole('button', { name: /actions for Jhereg/i }))
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }))
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(client.deleteHistory).not.toHaveBeenCalled()
    expect(screen.getByText('Jhereg')).toBeInTheDocument()
  })

  it('shows the header Import history button linking to /import', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Only Book')])
    renderView()
    await screen.findByText('Only Book')
    expect(screen.getByRole('link', { name: /import history/i })).toHaveAttribute('href', '/import')
  })
})
