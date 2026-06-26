import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router'
import Nav from './Nav'

describe('Nav', () => {
  it('renders the six primary tabs as links with accessible names', () => {
    render(<Nav />, { wrapper: MemoryRouter })
    for (const name of ['Chat', 'History', 'Picks', 'Analysis', 'Add', 'Libraries']) {
      expect(screen.getByRole('link', { name })).toBeInTheDocument()
    }
  })

  it('links each tab to its route', () => {
    render(<Nav />, { wrapper: MemoryRouter })
    expect(screen.getByRole('link', { name: 'Chat' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: 'History' })).toHaveAttribute('href', '/history')
    expect(screen.getByRole('link', { name: 'Picks' })).toHaveAttribute('href', '/recommendations')
    expect(screen.getByRole('link', { name: 'Analysis' })).toHaveAttribute('href', '/analysis')
    expect(screen.getByRole('link', { name: 'Add' })).toHaveAttribute('href', '/add')
    expect(screen.getByRole('link', { name: 'Libraries' })).toHaveAttribute('href', '/settings')
  })
})
