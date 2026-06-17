import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router'
import type { HistoryItem } from '../api/client'

vi.mock('../auth/firebase', () => ({ getIdToken: vi.fn().mockResolvedValue(null) }))
vi.mock('../api/client')
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
})
