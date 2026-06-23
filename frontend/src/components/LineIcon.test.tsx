import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import LineIcon from './LineIcon'

describe('LineIcon', () => {
  it('renders an aria-hidden svg on a 24-unit viewBox', () => {
    const { container } = render(<LineIcon name="chat" />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg!.getAttribute('viewBox')).toBe('0 0 24 24')
    expect(svg!.getAttribute('aria-hidden')).toBe('true')
  })

  it('renders the analysis glyph as four line segments', () => {
    const { container } = render(<LineIcon name="analysis" />)
    expect(container.querySelectorAll('line').length).toBe(4)
  })

  it('honours the size prop', () => {
    const { container } = render(<LineIcon name="add" size={18} />)
    expect(container.querySelector('svg')!.getAttribute('width')).toBe('18')
  })
})
