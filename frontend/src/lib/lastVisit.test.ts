import { beforeEach, describe, it, expect } from 'vitest'
import { computeNewIds, markSeen } from './lastVisit'

beforeEach(() => localStorage.clear())

describe('computeNewIds', () => {
  it('treats all ids as new on first visit', () => {
    expect(computeNewIds('recs', ['a', 'b'])).toEqual(new Set(['a', 'b']))
  })
  it('returns only unseen ids after markSeen', () => {
    markSeen('recs', ['a', 'b'])
    expect(computeNewIds('recs', ['a', 'b', 'c'])).toEqual(new Set(['c']))
  })
  it('is namespaced by key', () => {
    markSeen('recs', ['a'])
    expect(computeNewIds('history', ['a'])).toEqual(new Set(['a']))
  })
})
