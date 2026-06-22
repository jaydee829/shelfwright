const PATTERNS: [string, RegExp][] = [
  ['fantasy', /fantas|wuxia/],
  ['dystopian', /dystop/],
  ['scifi', /sci-?fi|science.?fiction|\bspace\b|alien/],
  ['horror', /horror|occult|paranormal/],
  ['mystery', /mystery|crime|detective|noir/],
  ['thriller', /thriller|suspense/],
  ['romance', /romance/],
  ['war', /\bwar\b|military/],
  ['lgbtq', /lgbtq|queer/],
  ['young-adult', /young.?adult|\bya\b/],
  ['historical', /historical|\bhistory\b/],
  ['literary', /literary|literature|classic/],
  ['adventure', /adventur|action/],
]

function strip(g: string): string {
  return g.toLowerCase().replace(/-[0-9a-f-]{20,}$/, '').trim()
}

export function canonicalizeGenre(genres: string[] | undefined): string | null {
  if (!genres?.length) return null
  const norm = genres.map(strip)
  for (const [key, re] of PATTERNS) if (norm.some((g) => re.test(g))) return key
  return null
}
