import type { Ranked } from '../api/client'

const LEADING_ARTICLE = /^(the|a|an)\s+/i
const ARTICLE_ONLY = /^(the|a|an)$/i

/** Cloud-local preprocessing for trope/style labels. Splits '/'-joined names,
 * strips a single leading article, collapses whitespace, drops empty/article-only
 * parts, then merges case-insensitive duplicates summing their counts. The full
 * names are preserved everywhere else in the app. Sorted by count desc. */
export function prepareCloudWords(items: Ranked[]): Ranked[] {
  const merged = new Map<string, Ranked>()
  for (const item of items) {
    for (const rawPart of item.name.split('/')) {
      const cleaned = rawPart.trim().replace(/\s+/g, ' ').replace(LEADING_ARTICLE, '').trim()
      if (!cleaned || ARTICLE_ONLY.test(cleaned)) continue
      const key = cleaned.toLowerCase()
      const existing = merged.get(key)
      if (existing) existing.count += item.count
      else merged.set(key, { name: cleaned, count: item.count })
    }
  }
  return [...merged.values()].sort((a, b) => b.count - a.count)
}
