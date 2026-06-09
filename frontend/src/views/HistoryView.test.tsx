import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ getHistory: vi.fn() }))

import { getHistory } from '../api/client'
import HistoryView from './HistoryView'

describe('HistoryView', () => {
  afterEach(() => vi.clearAllMocks())

  it('renders the reading log', async () => {
    vi.mocked(getHistory).mockResolvedValue([
      { id: 'h1', title: 'Dune', authors: ['Herbert'], date_completed: '2026-05-01', rating: 5, format: 'audiobook' },
    ])
    render(<HistoryView />)
    expect(await screen.findByText('Dune')).toBeInTheDocument()
    expect(screen.getByText(/Herbert/)).toBeInTheDocument()
  })

  it('shows an empty state when nothing has been read', async () => {
    vi.mocked(getHistory).mockResolvedValue([])
    render(<HistoryView />)
    expect(await screen.findByText(/nothing here yet/i)).toBeInTheDocument()
  })
})
