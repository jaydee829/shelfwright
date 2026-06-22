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

const LABELS: Record<string, string> = {
  fantasy: 'Fantasy', scifi: 'Science Fiction', adventure: 'Adventure', mystery: 'Mystery',
  romance: 'Romance', horror: 'Horror', thriller: 'Thriller', literary: 'Literary',
  historical: 'Historical', 'young-adult': 'Young Adult', lgbtq: 'LGBTQ', war: 'War',
  dystopian: 'Dystopian', other: 'Other',
}

// path data copied verbatim from spec Appendix A
const PATHS: Record<string, string> = {
  fantasy: '<path d="M4 20V8H6.5V11H8.5V8H11V11H13V8H15.5V11H17.5V8H20V20Z"/><path d="M10 20v-5a2 2 0 0 1 4 0v5"/>',
  scifi: '<path d="M12 2.5c2.6 2 4 5.2 4 9 0 2-.9 3.8-2 5H10c-1.1-1.2-2-3-2-5 0-3.8 1.4-7 4-9z"/><circle cx="12" cy="9.5" r="1.5"/><path d="M8.5 16c-1.2 1-1.8 2.6-1.8 4 1.5-.3 2.6-1 3.3-2M15.5 16c1.2 1 1.8 2.6 1.8 4-1.5-.3-2.6-1-3.3-2"/>',
  adventure: '<path d="M9 4 3 6.5v13.5l6-2.5 6 2.5 6-2.5V3.5L15 6 9 4z"/><path d="M9 4v13.5M15 6v13.5"/><path d="M11.5 10.5l1.5 1.5M13 10.5l-1.5 1.5"/>',
  mystery: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/>',
  romance: '<path d="M12 20S4 14.5 4 9.7A3.8 3.8 0 0 1 12 7a3.8 3.8 0 0 1 8 2.7C20 14.5 12 20 12 20z"/>',
  horror: '<path d="M5 11a7 7 0 0 1 14 0c0 2.3-1 3.6-2.2 4.3V18a1 1 0 0 1-1 1H8.2a1 1 0 0 1-1-1v-2.7C6 14.6 5 13.3 5 11z"/><circle cx="9.6" cy="11.2" r="1.4"/><circle cx="14.4" cy="11.2" r="1.4"/><path d="M11 19v-2M13 19v-2"/>',
  thriller: '<path d="M13 2 5 13h5l-1 9 8-12h-5l1-8z"/>',
  literary: '<path d="M5 19C7 11 11 6.5 19 5c-1 7-5 12-12 13.5z"/><path d="M7.5 17 17 7.5"/><path d="M5 19l-1.6 1.6"/><path d="M9.5 17.6l1.4-1M12 16.8l1.4-1"/>',
  historical: '<path d="M5 21h14M6.5 21V9.5M17.5 21V9.5M5 9.5h14M6 9.5 8 6h8l2 3.5M9.5 21V9.5M14.5 21V9.5"/>',
  'young-adult': '<path d="M12 21v-7"/><path d="M12 14c-.5-3-3-4.5-6-4.5.2 3 2.5 5 6 4.5z"/><path d="M12 12c.4-2.6 2.6-4 5.5-3.8C17.3 10.8 15 12.3 12 12z"/>',
  lgbtq: '<path d="M3 18a9 9 0 0 1 18 0"/><path d="M6 18a6 6 0 0 1 12 0"/><path d="M9 18a3 3 0 0 1 6 0"/>',
  war: '<path d="M6 18Q13.5 11 18.5 5.5"/><path d="M4.7 16.7 7.3 19.3"/><path d="M6 18 4.9 19.1"/><circle cx="4.5" cy="19.5" r=".8"/><path d="M18 18Q10.5 11 5.5 5.5"/><path d="M19.3 16.7 16.7 19.3"/><path d="M18 18 19.1 19.1"/><circle cx="19.5" cy="19.5" r=".8"/>',
  other: '<path d="M12 3 13.7 10.3 21 12 13.7 13.7 12 21 10.3 13.7 3 12 10.3 10.3Z"/>',
}

function strip(g: string): string {
  return g.toLowerCase().replace(/-[0-9a-f-]{20,}$/, '').trim()
}

export function canonicalizeGenre(genres: string[] | undefined): string | null {
  if (!genres?.length) return null
  const norm = genres.map(strip)
  for (const [key, re] of PATTERNS) if (norm.some((g) => re.test(g))) return key
  return null
}

export function GenreIcon({ genres, className }: { genres?: string[]; className?: string }) {
  const key = canonicalizeGenre(genres) ?? 'other'
  return (
    <svg
      className={className}
      viewBox="0 0 24 24" width="22" height="22"
      fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinejoin="round" strokeLinecap="round"
      role="img" aria-label={LABELS[key]}
      dangerouslySetInnerHTML={{ __html: PATHS[key] }}
    />
  )
}
