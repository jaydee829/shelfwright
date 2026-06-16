import { describe, expect, it, vi } from 'vitest'
import { _STEP_LABELS, labelForActivity } from './activityLabels'

describe('labelForActivity', () => {
  it('maps an ADK tool-named specialist and a Claude agent-named one to the same pool', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0) // first phrase
    expect(labelForActivity('tool', 'Explorer')).toEqual({ text: 'Checking the stacks', stepKind: 'stage' })
    expect(labelForActivity('agent', 'explorer')).toEqual({ text: 'Checking the stacks', stepKind: 'stage' })
    vi.restoreAllMocks()
  })

  it('marks slow tools as milestones, stages as stages', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    expect(labelForActivity('tool', 'enrich_and_persist_work')?.stepKind).toBe('milestone')
    expect(labelForActivity('tool', 'Analyst')?.stepKind).toBe('stage')
    vi.restoreAllMocks()
  })

  it('hides unmapped tool/DB calls and blanks', () => {
    expect(labelForActivity('tool', 'search_internal_database')).toBeNull()
    expect(labelForActivity('tool', '')).toBeNull()
  })

  it('every pool is non-empty', () => {
    for (const v of Object.values(_STEP_LABELS)) expect(v.phrases.length).toBeGreaterThan(0)
  })

  it('picks from the pool by random index', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.99) // last phrase
    const pool = _STEP_LABELS['analyst'].phrases
    expect(labelForActivity('tool', 'Analyst')?.text).toBe(pool[pool.length - 1])
    vi.restoreAllMocks()
  })
})
