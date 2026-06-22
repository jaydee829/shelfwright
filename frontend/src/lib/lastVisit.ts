const PREFIX = 'seen:'

function read(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    return raw ? new Set(JSON.parse(raw) as string[]) : new Set()
  } catch {
    return new Set()
  }
}

/** Ids present now that were NOT seen on a previous visit. */
export function computeNewIds(key: string, ids: string[]): Set<string> {
  const seen = read(key)
  return new Set(ids.filter((id) => !seen.has(id)))
}

/** Record the currently-shown ids as seen (call after rendering, e.g. in an effect). */
export function markSeen(key: string, ids: string[]): void {
  try {
    const seen = read(key)
    const before = seen.size
    for (const id of ids) seen.add(id)
    if (seen.size === before) return // all already seen — skip the redundant localStorage write
    // Cap the stored set so it can't grow unbounded (localStorage quota); newest ids are last.
    localStorage.setItem(PREFIX + key, JSON.stringify([...seen].slice(-1000)))
  } catch {
    /* storage unavailable — degrade to no marker */
  }
}
