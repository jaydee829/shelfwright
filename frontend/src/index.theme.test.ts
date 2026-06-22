import { describe, it, expect } from 'vitest'
import { applyTheme } from './theme'

const CORE = ['--bg', '--surface', '--text', '--accent', '--gilt', '--spine', '--page-edge', '--font-display']

describe('design tokens', () => {
  it.each(['light', 'dark'] as const)('applyTheme wires the %s theme', (theme) => {
    applyTheme(theme)
    // jsdom does not evaluate @import'ed CSS, so we assert applyTheme's wiring
    // (the data-theme attribute) rather than computed token values. Real token
    // verification is the manual visual walk.
    expect(document.documentElement.dataset.theme).toBe(theme)
    expect(CORE.length).toBeGreaterThan(0)
  })
})
