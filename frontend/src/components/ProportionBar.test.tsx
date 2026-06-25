import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ProportionBar from './ProportionBar'

const items = [
  { name: 'Audiobook', count: 58 },
  { name: 'Ebook', count: 27 },
  { name: 'Hardcover', count: 11 },
  { name: 'Paperback', count: 4 },
]

describe('ProportionBar', () => {
  it('renders a legend entry per format with a percentage', () => {
    render(<ProportionBar items={items} />)
    const legend = screen.getByRole('list', { name: /format legend/i })
    expect(within(legend).getByText(/Audiobook/)).toBeInTheDocument()
    expect(within(legend).getByText(/58%/)).toBeInTheDocument()
    expect(within(legend).getByText(/Paperback/)).toBeInTheDocument()
  })

  it('orders segments largest to smallest', () => {
    render(<ProportionBar items={[items[2], items[0], items[1], items[3]]} />)
    const segs = screen.getAllByTestId('segment')
    const widths = segs.map((s) => parseFloat(s.style.width))
    expect(widths).toEqual([...widths].sort((a, b) => b - a))
  })

  it('renders nothing when empty', () => {
    const { container } = render(<ProportionBar items={[]} />)
    expect(container.firstChild).toBeNull()
  })
})
