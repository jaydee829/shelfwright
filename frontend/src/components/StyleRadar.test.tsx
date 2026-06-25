import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { StyleRadar as Radar } from '../api/client'
import StyleRadar from './StyleRadar'

const full: Radar = {
  pace: 0.7, density: 0.4, depth: 0.6, inner_focus: 0.5,
  humor: 0.2, warmth: 0.7, lexicon: 0.5, world_building: 0.8,
}

describe('StyleRadar', () => {
  it('renders an accessible summary naming the axes when data is present', () => {
    render(<StyleRadar radar={full} />)
    const fig = screen.getByRole('img', { name: /shape of your reading/i })
    expect(fig).toBeInTheDocument()
    expect(fig.getAttribute('aria-label')).toMatch(/pace/i)
  })

  it('shows the gathering message when radar is undefined', () => {
    render(<StyleRadar radar={undefined} />)
    expect(screen.getByText(/gathering your style/i)).toBeInTheDocument()
  })

  it('shows the gathering message when fewer than three axes have data', () => {
    render(<StyleRadar radar={{ ...emptyRadar, pace: 0.5, humor: 0.3 }} />)
    expect(screen.getByText(/gathering your style/i)).toBeInTheDocument()
  })
})

const emptyRadar: Radar = {
  pace: null, density: null, depth: null, inner_focus: null,
  humor: null, warmth: null, lexicon: null, world_building: null,
}
