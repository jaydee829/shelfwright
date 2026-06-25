import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import BookLinks from './BookLinks'

describe('BookLinks', () => {
  const links = [
    { kind: 'libby' as const, label: 'KCLS on Libby', url: 'https://libby/x' },
    { kind: 'amazon' as const, label: 'Amazon', url: 'https://amazon/x' },
  ]

  it('renders links always, even with no availability', () => {
    render(<BookLinks availability={{ links, libby: [] }} />)
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.queryByText(/available now/i)).not.toBeInTheDocument()
  })

  it('renders an availability badge when libby data is present', () => {
    render(<BookLinks availability={{
      links,
      libby: [{ library: 'KCLS', slug: 'kcls', formats: [
        { format: 'Audiobook', available: true, copies_owned: 20, copies_available: 2, holds_ratio: 0, wait_days: 0 },
      ] }],
    }} />)
    expect(screen.getAllByText(/KCLS/).length).toBeGreaterThan(0)
    expect(screen.getByText(/available now/i)).toBeInTheDocument()
  })

  it('renders nothing when availability is undefined (still loading)', () => {
    const { container } = render(<BookLinks availability={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })
})
