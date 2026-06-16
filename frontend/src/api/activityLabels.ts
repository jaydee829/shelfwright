export type StepKind = 'stage' | 'milestone'

export interface ActivityStep {
  id: number
  text: string
  stepKind: StepKind
  status: 'running' | 'done'
}

export interface ActivityLabel {
  text: string
  stepKind: StepKind
}

interface StepPool {
  phrases: string[]
  stepKind: StepKind
}

// Keyed by lower-cased activity `detail` so it covers ADK's ("tool","Explorer") and Claude's
// ("agent","explorer") uniformly. Unmapped details are hidden. All copy is original
// library/fantasy-themed homage (not quotations from any book).
const STEP_LABELS: Record<string, StepPool> = {
  analyst: {
    stepKind: 'stage',
    phrases: [
      'Analyzing your tastes',
      'Comparing tropes',
      'Consulting the card catalog',
      'Cross-referencing your shelves',
      'Reading the runes of your history',
    ],
  },
  explorer: {
    stepKind: 'stage',
    phrases: [
      'Checking the stacks',
      'Searching L-space',
      'Wandering the infinite shelves',
      'Following a trail between worlds',
      'Off to the far reading-rooms',
    ],
  },
  critic: {
    stepKind: 'stage',
    phrases: ['Ranking matches', 'Weighing the contenders', 'Sorting wheat from chaff', 'Judging covers and contents'],
  },
  librarian: {
    stepKind: 'stage',
    phrases: ['Ook!', 'Tending the collection', 'Consulting the Head Librarian', 'Shhh… working'],
  },
  get_recommendation_candidates: {
    stepKind: 'milestone',
    phrases: [
      'Checking your recommendations',
      'I think I have just the thing',
      'Pulling a few from the back room',
      'Dusting off some candidates',
    ],
  },
  enrich_and_persist_work: {
    stepKind: 'milestone',
    phrases: ['Cataloging a discovery', 'Filing it under the right tropes', 'Enriching a new find'],
  },
}

/** Map a raw (kind, detail) activity event to a display label, or null to hide it.
 *  `kind` is accepted for symmetry with the SSE callback; classification is by `detail`. */
export function labelForActivity(_kind: string, detail: string): ActivityLabel | null {
  const pool = STEP_LABELS[(detail || '').toLowerCase()]
  if (!pool) return null
  const text = pool.phrases[Math.floor(Math.random() * pool.phrases.length)]
  return { text, stepKind: pool.stepKind }
}

export const _STEP_LABELS = STEP_LABELS // exported for the non-empty-pool guard test
