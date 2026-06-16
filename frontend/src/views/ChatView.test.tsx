import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatHandlers } from '../api/client'

vi.mock('../api/client', () => ({
  getCurrentConversation: vi.fn(),
  newConversation: vi.fn(),
  streamChat: vi.fn(),
}))

import { getCurrentConversation, newConversation, streamChat } from '../api/client'
import ChatView from './ChatView'

describe('ChatView', () => {
  beforeEach(() => {
    vi.mocked(getCurrentConversation).mockResolvedValue({ id: 'c1', messages: [] })
    vi.mocked(newConversation).mockResolvedValue({ id: 'c2', messages: [] })
  })
  afterEach(() => vi.clearAllMocks())

  it('loads and shows prior messages on resume', async () => {
    vi.mocked(getCurrentConversation).mockResolvedValue({
      id: 'c1',
      messages: [
        { role: 'user', content: 'hi' },
        { role: 'assistant', content: 'hello friend' },
      ],
    })
    render(<ChatView />)
    expect(await screen.findByText('hello friend')).toBeInTheDocument()
  })

  it('sends a message and streams activity then reply', async () => {
    vi.mocked(streamChat).mockImplementation(async (_msg: string, h: ChatHandlers) => {
      h.onActivity('search', 'Explorer is searching')
      h.onText('Try Dune.')
    })
    render(<ChatView />)
    await screen.findByPlaceholderText(/ask the librarian/i)

    await userEvent.type(screen.getByPlaceholderText(/ask the librarian/i), 'recommend a book')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    expect(await screen.findByText('recommend a book')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Try Dune.')).toBeInTheDocument())
    expect(vi.mocked(streamChat)).toHaveBeenCalledWith('recommend a book', expect.anything())
  })

  it('starts a new chat, clearing the thread', async () => {
    vi.mocked(getCurrentConversation).mockResolvedValue({
      id: 'c1',
      messages: [{ role: 'assistant', content: 'old thread' }],
    })
    render(<ChatView />)
    expect(await screen.findByText('old thread')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /new chat/i }))
    await waitFor(() => expect(screen.queryByText('old thread')).not.toBeInTheDocument())
    expect(vi.mocked(newConversation)).toHaveBeenCalled()
  })

  it('shows a live trail step for a mapped agent, then a collapsed trail after the reply', async () => {
    vi.mocked(streamChat).mockImplementation(async (_msg: string, h: ChatHandlers) => {
      h.onActivity('tool', 'Explorer') // maps to an Explorer phrase (stage)
      h.onText('Try Dune.')
    })
    render(<ChatView />)
    await screen.findByPlaceholderText(/ask the librarian/i)
    await userEvent.type(screen.getByPlaceholderText(/ask the librarian/i), 'recommend a book')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(screen.getByText('Try Dune.')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /how i found these/i })).toBeInTheDocument()
  })

  it('hides unmapped tool calls (no trail recorded)', async () => {
    vi.mocked(streamChat).mockImplementation(async (_msg: string, h: ChatHandlers) => {
      h.onActivity('tool', 'search_internal_database') // unmapped -> hidden
      h.onText('Done.')
    })
    render(<ChatView />)
    await screen.findByPlaceholderText(/ask the librarian/i)
    await userEvent.type(screen.getByPlaceholderText(/ask the librarian/i), 'hi')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(screen.getByText('Done.')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /how i found these/i })).not.toBeInTheDocument()
  })
})
