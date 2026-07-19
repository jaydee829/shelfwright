import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ addBook: vi.fn() }))

import { addBook } from '../api/client'
import AddBookView from './AddBookView'

function renderView() {
  return render(
    <MemoryRouter>
      <AddBookView />
    </MemoryRouter>,
  )
}

describe('AddBookView', () => {
  // Use the single-use `...Once` mock variants. A persistent `mockResolvedValue`
  // overridden by a persistent `mockRejectedValue` leaves a rejected promise that
  // vitest reports as an unhandled error (vitest-dev/vitest#1692). Once-variants are
  // consumed exactly once, so nothing lingers between tests.
  afterEach(() => vi.clearAllMocks())

  const okResult = {
    work_id: 'w1', title: 'Dune', read_number: 1, already_logged: false, enrichment_enqueued: true,
    pick_resolved: false,
  }

  it('prefills the date-finished field with today', () => {
    renderView()
    const today = new Date().toISOString().slice(0, 10)
    expect(screen.getByLabelText(/date finished/i)).toHaveValue(today)
  })

  it('submits the form and shows a confirmation', async () => {
    vi.mocked(addBook).mockResolvedValueOnce(okResult)
    renderView()
    await userEvent.type(screen.getByLabelText(/title/i), 'Dune')
    await userEvent.type(screen.getByLabelText(/author/i), 'Frank Herbert')
    await userEvent.click(screen.getByRole('button', { name: /add to history/i }))

    expect(vi.mocked(addBook)).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Dune', author: 'Frank Herbert' }),
    )
    expect(await screen.findByText(/Enriching in the background/i)).toBeInTheDocument()
  })

  it('shows an error when the book is not found', async () => {
    vi.mocked(addBook).mockRejectedValueOnce(new Error('addBook → 404'))
    renderView()
    await userEvent.type(screen.getByLabelText(/title/i), 'Ghost')
    await userEvent.type(screen.getByLabelText(/author/i), 'Nobody')
    await userEvent.click(screen.getByRole('button', { name: /add to history/i }))
    expect(await screen.findByText(/couldn.t add/i)).toBeInTheDocument()
  })

  it('disables submit until title and author are filled', () => {
    renderView()
    expect(screen.getByRole('button', { name: /add to history/i })).toBeDisabled()
  })

  it('mentions the cleared pick when the server resolved one', async () => {
    vi.mocked(addBook).mockResolvedValueOnce({ ...okResult, pick_resolved: true })
    renderView()
    await userEvent.type(screen.getByLabelText(/title/i), 'Dune')
    await userEvent.type(screen.getByLabelText(/author/i), 'Frank Herbert')
    await userEvent.click(screen.getByRole('button', { name: /add to history/i }))
    expect(await screen.findByText(/Also cleared it from your Picks/i)).toBeInTheDocument()
  })

  it('does not mention Picks when nothing was resolved', async () => {
    vi.mocked(addBook).mockResolvedValueOnce(okResult)
    renderView()
    await userEvent.type(screen.getByLabelText(/title/i), 'Dune')
    await userEvent.type(screen.getByLabelText(/author/i), 'Frank Herbert')
    await userEvent.click(screen.getByRole('button', { name: /add to history/i }))
    await screen.findByText(/Enriching in the background/i)
    expect(screen.queryByText(/cleared it from your Picks/i)).toBeNull()
  })
})
