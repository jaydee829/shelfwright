import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Hoisted handles so the firebase mock and the tests share the same callbacks.
const h = vi.hoisted(() => ({
  authCb: null as ((user: unknown) => void) | null,
}))

vi.mock('./firebase', () => ({
  onAuth: (cb: (user: unknown) => void) => {
    h.authCb = cb
    return () => {}
  },
  signInWithGoogle: vi.fn(),
  signOutUser: vi.fn(),
}))

vi.mock('../api/client', () => ({
  probeAccess: vi.fn(),
}))

import { probeAccess } from '../api/client'
import { AuthProvider, useAuth } from './AuthContext'

function Probe() {
  const { status, user } = useAuth()
  return <div>status:{status} user:{user ? user.email : 'none'}</div>
}

function renderProvider() {
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  )
}

describe('AuthContext', () => {
  beforeEach(() => {
    h.authCb = null
    vi.mocked(probeAccess).mockReset()
  })
  afterEach(() => vi.clearAllMocks())

  it('starts in loading then resolves to signedOut when no user', async () => {
    renderProvider()
    expect(screen.getByText(/status:loading/)).toBeInTheDocument()
    h.authCb!(null)
    await waitFor(() => expect(screen.getByText(/status:signedOut/)).toBeInTheDocument())
  })

  it('probes the backend and becomes ready for an invited user', async () => {
    vi.mocked(probeAccess).mockResolvedValue('ready')
    renderProvider()
    h.authCb!({ email: 'friend@example.com' })
    await waitFor(() => expect(screen.getByText(/status:ready/)).toBeInTheDocument())
    expect(screen.getByText(/user:friend@example.com/)).toBeInTheDocument()
  })

  it('becomes notInvited when the backend rejects with 403', async () => {
    vi.mocked(probeAccess).mockResolvedValue('notInvited')
    renderProvider()
    h.authCb!({ email: 'stranger@example.com' })
    await waitFor(() => expect(screen.getByText(/status:notInvited/)).toBeInTheDocument())
  })

  it('stays on loading when the backend probe errors (transient), not notInvited', async () => {
    vi.mocked(probeAccess).mockResolvedValue('error')
    renderProvider()
    h.authCb!({ email: 'friend@example.com' })
    await waitFor(() => expect(vi.mocked(probeAccess)).toHaveBeenCalled())
    expect(screen.queryByText(/status:notInvited/)).not.toBeInTheDocument()
    expect(screen.getByText(/status:loading/)).toBeInTheDocument()
  })
})
