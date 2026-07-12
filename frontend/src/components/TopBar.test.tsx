import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'a@b.com', displayName: 'A' }, signOut: vi.fn() }),
}))

import TopBar from './TopBar'

afterEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

describe('TopBar theme toggle', () => {
  it('flips data-theme and the label on click', async () => {
    document.documentElement.dataset.theme = 'light'
    render(<TopBar />)
    await userEvent.click(screen.getByRole('button', { name: /switch to dark mode/i }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(screen.getByRole('button', { name: /switch to light mode/i })).toBeInTheDocument()
  })
})

describe('TopBar branding', () => {
  it('shows the Shelfwright product name', () => {
    render(<TopBar />)
    expect(screen.getByText('Shelfwright')).toBeInTheDocument()
  })
})
