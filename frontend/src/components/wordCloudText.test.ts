import { describe, expect, it } from 'vitest'
import { prepareCloudWords } from './wordCloudText'

describe('prepareCloudWords', () => {
  it('strips a single leading article', () => {
    expect(prepareCloudWords([{ name: 'The Seer', count: 5 }])).toEqual([{ name: 'Seer', count: 5 }])
    expect(prepareCloudWords([{ name: 'A Court of Thorns', count: 2 }])).toEqual([
      { name: 'Court of Thorns', count: 2 },
    ])
  })

  it('does not strip an article mid-word', () => {
    expect(prepareCloudWords([{ name: 'Theseus', count: 1 }])).toEqual([{ name: 'Theseus', count: 1 }])
  })

  it('splits on "/" and keeps both sides', () => {
    const out = prepareCloudWords([{ name: 'Enemies / Lovers', count: 4 }])
    expect(out).toEqual([
      { name: 'Enemies', count: 4 },
      { name: 'Lovers', count: 4 },
    ])
  })

  it('merges case-insensitive duplicates summing counts', () => {
    const out = prepareCloudWords([
      { name: 'Enemies / Lovers', count: 4 },
      { name: 'lovers', count: 3 },
    ])
    expect(out).toEqual([
      { name: 'Lovers', count: 7 },
      { name: 'Enemies', count: 4 },
    ])
  })

  it('sorts by count descending', () => {
    const out = prepareCloudWords([
      { name: 'Rare', count: 1 },
      { name: 'Common', count: 9 },
    ])
    expect(out.map((w) => w.name)).toEqual(['Common', 'Rare'])
  })

  it('collapses internal whitespace and trims', () => {
    expect(prepareCloudWords([{ name: '  Slow   Burn  ', count: 2 }])).toEqual([
      { name: 'Slow Burn', count: 2 },
    ])
  })

  it('drops empty and article-only parts', () => {
    expect(prepareCloudWords([{ name: 'The / Seer', count: 5 }])).toEqual([{ name: 'Seer', count: 5 }])
    expect(prepareCloudWords([{ name: ' / ', count: 5 }])).toEqual([])
  })

  it('returns [] for empty input', () => {
    expect(prepareCloudWords([])).toEqual([])
  })

  it('does not mutate the input objects', () => {
    const input = [{ name: 'Found Family', count: 3 }]
    prepareCloudWords(input)
    expect(input).toEqual([{ name: 'Found Family', count: 3 }])
  })
})
