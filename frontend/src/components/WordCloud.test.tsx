import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// d3-cloud needs a real <canvas>; jsdom can't lay out, so mock the hook and
// assert the accessible summary instead of positioned text.
vi.mock('@isoterik/react-word-cloud', () => ({
  useWordCloud: () => ({ computedWords: [], isLoading: false }),
}))

import WordCloud from './WordCloud'

describe('WordCloud', () => {
  it('exposes an accessible summary of the preprocessed words', () => {
    render(
      <WordCloud
        items={[
          { name: 'The Seer', count: 10 },
          { name: 'Enemies / Lovers', count: 6 },
        ]}
      />,
    )
    const label = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(label).toContain('Seer')
    expect(label).toContain('Enemies')
    expect(label).toContain('Lovers')
    expect(label).not.toContain('The Seer')
  })

  it('reports the merged word count (split + summed duplicates)', () => {
    render(
      <WordCloud
        items={[
          { name: 'Alpha / Beta', count: 5 },
          { name: 'Beta', count: 2 },
        ]}
      />,
    )
    // 'Alpha' (5) and 'Beta' (5+2=7) -> 2 distinct words
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('Word cloud of 2 words')
  })

  it('renders nothing when empty', () => {
    const { container } = render(<WordCloud items={[]} />)
    expect(container.firstChild).toBeNull()
  })
})
