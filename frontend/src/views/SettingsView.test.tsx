import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import SettingsView from './SettingsView'
import * as client from '../api/client'

vi.mock('../auth/firebase', () => ({ getIdToken: vi.fn().mockResolvedValue(null) }))
vi.mock('../api/client')

describe('SettingsView', () => {
  beforeEach(() => {
    vi.mocked(client.getMyLibraries).mockResolvedValue([{ slug: 'kcls', name: 'KCLS' }])
    vi.mocked(client.searchLibraries).mockResolvedValue([{ slug: 'spl', name: 'Seattle PL' }])
    vi.mocked(client.saveMyLibraries).mockResolvedValue(undefined)
  })

  it('loads and shows saved libraries', async () => {
    render(<SettingsView />)
    expect(await screen.findByText('KCLS')).toBeInTheDocument()
  })

  it('searches, adds, and saves a library', async () => {
    render(<SettingsView />)
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'seattle' } })
    fireEvent.click(await screen.findByText(/add/i))
    fireEvent.click(screen.getByText(/save/i))
    await waitFor(() => expect(client.saveMyLibraries).toHaveBeenCalled())
    const saved = vi.mocked(client.saveMyLibraries).mock.calls[0][0]
    expect(saved.map((l) => l.slug)).toContain('spl')
  })
})
