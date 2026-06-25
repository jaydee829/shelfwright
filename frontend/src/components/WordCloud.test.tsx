import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import WordCloud from './WordCloud'

const items = [
  { name: 'Found Family', count: 30 },
  { name: 'Slow Burn', count: 18 },
  { name: 'Chosen One', count: 3 },
]

describe('WordCloud', () => {
  it('renders every entry as readable text', () => {
    render(<WordCloud items={items} />)
    for (const it of items) expect(screen.getByText(it.name)).toBeInTheDocument()
  })

  it('scales the most frequent larger than the least frequent', () => {
    render(<WordCloud items={items} />)
    const big = screen.getByText('Found Family')
    const small = screen.getByText('Chosen One')
    expect(parseFloat(big.style.fontSize)).toBeGreaterThan(parseFloat(small.style.fontSize))
  })

  it('assigns a non-empty color to the smallest word (never faded out)', () => {
    render(<WordCloud items={items} />)
    expect(screen.getByText('Chosen One').style.color).toMatch(/var\(--cat-/)
  })

  it('renders nothing when empty', () => {
    const { container } = render(<WordCloud items={[]} />)
    expect(container.firstChild).toBeNull()
  })
})
