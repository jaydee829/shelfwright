import { describe, expect, it } from 'vitest'
import { MIN_PX, colorClass, hashString, maxPx, rotateFor, sizeFor } from './wordCloudLayout'

describe('maxPx (width-responsive ceiling)', () => {
  it('shrinks on narrow widths and caps on wide ones', () => {
    expect(maxPx(200)).toBe(36) // floor
    expect(maxPx(375)).toBe(41) // round(375*0.11)=41
    expect(maxPx(1280)).toBe(60) // cap
  })
})

describe('sizeFor (power curve)', () => {
  it('puts the least frequent at the floor and the most frequent at the responsive max', () => {
    expect(sizeFor(2, 2, 20, 1280)).toBeCloseTo(MIN_PX) // norm 0 -> MIN
    expect(sizeFor(20, 2, 20, 1280)).toBeCloseTo(60) // norm 1 -> maxPx(1280)
  })

  it('returns the midpoint when all counts are equal', () => {
    const mid = MIN_PX + 0.5 ** 1.4 * (maxPx(1280) - MIN_PX)
    expect(sizeFor(7, 7, 7, 1280)).toBeCloseTo(mid)
  })

  it('scales the max down on a narrow column', () => {
    expect(sizeFor(20, 2, 20, 375)).toBeCloseTo(41)
  })

  it('clamps an out-of-range count instead of returning NaN', () => {
    expect(sizeFor(100, 2, 20, 1280)).toBeCloseTo(60) // above hi -> max
    expect(sizeFor(0, 2, 20, 1280)).toBeCloseTo(MIN_PX) // below lo -> floor
  })
})

describe('rotateFor (deterministic ~70/30)', () => {
  it('is deterministic and only ever 0 or 90', () => {
    for (const t of ['Found Family', 'Slow Burn', 'Seer', 'Lovers']) {
      const r = rotateFor(t)
      expect([0, 90]).toContain(r)
      expect(rotateFor(t)).toBe(r)
    }
  })
})

describe('hashString', () => {
  it('is deterministic and non-negative', () => {
    expect(hashString('abc')).toBe(hashString('abc'))
    expect(hashString('abc')).toBeGreaterThanOrEqual(0)
  })
})

describe('colorClass', () => {
  it('cycles cat-1..6', () => {
    expect(colorClass(0)).toBe('cat-1')
    expect(colorClass(5)).toBe('cat-6')
    expect(colorClass(6)).toBe('cat-1')
  })
})
