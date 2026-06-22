import { render } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { GenreIcon } from './GenreIcon'
import { canonicalizeGenre } from './genreUtils'

describe('canonicalizeGenre', () => {
  it('maps urban-fantasy to fantasy', () => expect(canonicalizeGenre(['urban-fantasy'])).toBe('fantasy'))
  it('strips UUID suffix slugs', () => expect(canonicalizeGenre(['high-fantasy-dfcd50f5-2789-45fc-9ee3-3e4354620e62'])).toBe('fantasy'))
  it('maps science-fiction to scifi', () => expect(canonicalizeGenre(['science-fiction'])).toBe('scifi'))
  it('prefers dystopian over scifi', () => expect(canonicalizeGenre(['science-fiction', 'dystopian'])).toBe('dystopian'))
  it('maps classics to literary', () => expect(canonicalizeGenre(['classics'])).toBe('literary'))
  it('returns null for unknown/empty', () => {
    expect(canonicalizeGenre(['general'])).toBeNull()
    expect(canonicalizeGenre([])).toBeNull()
  })
})

describe('GenreIcon', () => {
  it('renders an svg with the genre name as accessible label', () => {
    const { container } = render(<GenreIcon genres={['fantasy']} />)
    const svg = container.querySelector('svg')
    expect(svg).toBeTruthy()
    expect(svg?.getAttribute('aria-label')).toBe('Fantasy')
  })
  it('renders the fallback star for unknown genre', () => {
    const { container } = render(<GenreIcon genres={['general']} />)
    expect(container.querySelector('svg')?.getAttribute('aria-label')).toBe('Other')
  })
  it('renders a non-empty icon body for every supported genre (guards against missing paths)', () => {
    const samples = [
      'fantasy', 'science-fiction', 'adventure', 'mystery', 'romance', 'horror',
      'thriller', 'classics', 'historical', 'young-adult', 'lgbtq', 'war', 'dystopian', 'general',
    ]
    for (const g of samples) {
      const { container } = render(<GenreIcon genres={[g]} />)
      const svg = container.querySelector('svg')!
      expect(svg.getAttribute('aria-label')).toBeTruthy()
      expect(svg.innerHTML.length).toBeGreaterThan(0)
    }
  })
  it('renders the dystopian icon with its label', () => {
    const { container } = render(<GenreIcon genres={['dystopian']} />)
    expect(container.querySelector('svg')?.getAttribute('aria-label')).toBe('Dystopian')
  })
})
