import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({ signIn: vi.fn() }),
}))

import SignIn from './SignIn'

describe('SignIn branding', () => {
  it('shows Shelfwright as the product name with the reading-companion subtitle', () => {
    render(<SignIn />)
    expect(screen.getByRole('heading', { name: /Shelfwright/ })).toBeInTheDocument()
    expect(screen.getByText('Your personal reading companion.')).toBeInTheDocument()
  })
})
