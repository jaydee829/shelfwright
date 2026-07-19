import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router'
import { ApiError, type HistoryItem } from '../api/client'

vi.mock('../auth/firebase', () => ({ getIdToken: vi.fn().mockResolvedValue(null) }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  updateHistory: vi.fn(),
  deleteHistory: vi.fn(),
  getHistory: vi.fn(),
}))
import * as client from '../api/client'
import HistoryEditView from './HistoryEditView'

const row: HistoryItem = {
  id: 'h1', title: 'Jhereg', authors: ['Steven Brust'], date_completed: '2019-03-14',
  rating: 4, format: 'ebook', notes: 'fun', genre: 'Fantasy', tropes: ['Antihero'],
}

function renderAt(state: HistoryItem | null) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/history/h1/edit', state }]}>
      <Routes>
        <Route path="/history/:id/edit" element={<HistoryEditView />} />
        <Route path="/history" element={<div>history-list</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('HistoryEditView', () => {
  it('prefills from router state and saves edits', async () => {
    vi.mocked(client.updateHistory).mockResolvedValueOnce({ ...row, rating: 5 })
    renderAt(row)
    expect(screen.getByDisplayValue('2019-03-14')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await waitFor(() =>
      expect(client.updateHistory).toHaveBeenCalledWith('h1', expect.objectContaining({ date_completed: '2019-03-14' })),
    )
    expect(await screen.findByText('history-list')).toBeInTheDocument()
  })

  it('redirects to history when opened with no router state', () => {
    renderAt(null)
    expect(screen.getByText('history-list')).toBeInTheDocument()
  })

  it('renders the format select prefilled with the current format', () => {
    renderAt(row)
    expect(screen.getByLabelText(/format/i)).toHaveValue('ebook')
  })

  it('sends format only when the user changes it', async () => {
    vi.mocked(client.updateHistory).mockResolvedValueOnce({ ...row, format: 'audiobook' })
    renderAt(row)
    await userEvent.selectOptions(screen.getByLabelText(/format/i), 'audiobook')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await waitFor(() =>
      expect(client.updateHistory).toHaveBeenCalledWith('h1', expect.objectContaining({ format: 'audiobook' })),
    )
  })

  it('omits format from the payload when unchanged', async () => {
    vi.mocked(client.updateHistory).mockResolvedValueOnce({ ...row })
    renderAt(row)
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await waitFor(() =>
      expect(client.updateHistory).toHaveBeenCalledWith(
        'h1',
        expect.not.objectContaining({ format: expect.anything() }),
      ),
    )
  })

  it('shows the server message on a 409 collision', async () => {
    // ...Once variant (vitest pitfall memory): a persistent mockRejectedValue leaks
    // an unhandled rejection into later tests.
    vi.mocked(client.updateHistory).mockRejectedValueOnce(
      new ApiError(409, 'You already logged this book as audiobook on 2019-03-14.'),
    )
    renderAt(row)
    await userEvent.selectOptions(screen.getByLabelText(/format/i), 'audiobook')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    expect(
      await screen.findByText('You already logged this book as audiobook on 2019-03-14.'),
    ).toBeInTheDocument()
  })

  it('falls back to the generic message on non-409 failures', async () => {
    vi.mocked(client.updateHistory).mockRejectedValueOnce(new Error('network'))
    renderAt(row)
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    expect(await screen.findByText(/couldn't save those changes/i)).toBeInTheDocument()
  })
})
