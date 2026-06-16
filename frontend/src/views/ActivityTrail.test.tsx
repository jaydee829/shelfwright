import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { CompletedActivityTrail, LiveActivityTrail } from './ActivityTrail'
import type { ActivityStep } from '../api/activityLabels'

const steps: ActivityStep[] = [
  { id: 1, text: 'Analyzing your tastes', stepKind: 'stage', status: 'done' },
  { id: 2, text: 'Checking the stacks', stepKind: 'stage', status: 'running' },
]

describe('ActivityTrail', () => {
  it('live trail shows every step', () => {
    render(<LiveActivityTrail steps={steps} />)
    expect(screen.getByText('Analyzing your tastes')).toBeInTheDocument()
    expect(screen.getByText('Checking the stacks')).toBeInTheDocument()
  })

  it('live trail renders nothing when empty', () => {
    const { container } = render(<LiveActivityTrail steps={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('completed trail starts collapsed, expands on click', async () => {
    render(<CompletedActivityTrail steps={steps} />)
    expect(screen.queryByText('Analyzing your tastes')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /how i found these \(2 steps\)/i }))
    expect(screen.getByText('Analyzing your tastes')).toBeInTheDocument()
  })

  it('completed trail renders nothing when empty', () => {
    const { container } = render(<CompletedActivityTrail steps={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
