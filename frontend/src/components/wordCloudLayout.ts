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
  // Clamp: a count outside [lo, hi] would make norm negative, and a negative base
  // to a fractional power (EXP) is NaN — which would break SVG rendering.
  const norm = hi === lo ? 0.5 : clamp((count - lo) / (hi - lo), 0, 1)
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
