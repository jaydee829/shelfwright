import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import GenreMoodBars from './GenreMoodBars'

const items = [
  { name: 'Fantasy', count: 58 },
  { name: 'Science Fiction', count: 47 },
]

describe('GenreMoodBars', () => {
  it('renders an accessible summary with the title and entries', () => {
    render(<GenreMoodBars title="Genres" items={items} />)
    const fig = screen.getByRole('img', { name: /genres/i })
    expect(fig.getAttribute('aria-label')).toMatch(/Fantasy 58/)
  })

  it('shows an empty message when there is no data', () => {
    render(<GenreMoodBars title="Moods" items={[]} />)
    expect(screen.getByText(/no data yet/i)).toBeInTheDocument()
  })
})
