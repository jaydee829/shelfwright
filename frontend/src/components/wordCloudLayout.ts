export const MIN_PX = 14
export const EXP = 1.4
export const LARGE_PX = 34 // size at/above which a word also gets bold weight

const MAX_FACTOR = 0.11
const MAX_FLOOR = 36
const MAX_CEIL = 60

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n))

/** Largest font size, derived from container width so it shrinks on mobile. */
export function maxPx(width: number): number {
  return clamp(Math.round(width * MAX_FACTOR), MAX_FLOOR, MAX_CEIL)
}

/** Frequency -> px via a power curve with a legible floor and width-responsive cap. */
export function sizeFor(count: number, lo: number, hi: number, width: number): number {
  const norm = hi === lo ? 0.5 : (count - lo) / (hi - lo)
  return MIN_PX + norm ** EXP * (maxPx(width) - MIN_PX)
}

/** Deterministic 32-bit FNV-1a-style string hash. */
export function hashString(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/** ~30% of words land vertical (90 deg), deterministically per word text. */
export function rotateFor(text: string): 0 | 90 {
  return hashString(text) % 10 < 3 ? 90 : 0
}

/** Cycles the categorical palette so every word (incl. the smallest) stays readable. */
export function colorClass(index: number): string {
  return `cat-${(index % 6) + 1}`
}

/** Seedable PRNG so the d3-cloud layout is stable across re-renders. */
export function mulberry32(seed: number): () => number {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
