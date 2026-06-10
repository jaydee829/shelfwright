import { getIdToken } from '../auth/firebase'

export interface HistoryItem {
  id: string
  title: string
  authors: string[]
  date_completed: string | null
  rating: number | null
  format: string | null
}

export interface Recommendation {
  id: string
  work_id: string
  title: string
  authors: string[]
  justification: string | null
  context: string | null
  suggested_at: string | null
  status: string
}

export interface Ranked {
  name: string
  count: number
}

export interface Analysis {
  snapshot: {
    total_read: number
    read_this_year: number
    average_rating: number | null
    distinct_authors: number
    formats: Ranked[]
  }
  genres: Ranked[]
  moods: Ranked[]
  top_tropes: Ranked[]
  authors: Ranked[]
  narrators: Ranked[]
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface Conversation {
  id: string
  messages: ChatMessage[]
}

/** fetch with the Firebase ID token attached. */
async function authedFetchRaw(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await getIdToken()
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return fetch(path, { ...init, headers })
}

async function getJson<T>(path: string): Promise<T> {
  const res = await authedFetchRaw(path)
  if (!res.ok) throw new Error(`${path} → ${res.status}`)
  return res.json() as Promise<T>
}

/** One lightweight authed call to decide invited-or-not. 200 → ready, 403 → notInvited,
 *  anything else → error (treated as not-ready by the caller). */
export async function probeAccess(): Promise<'ready' | 'notInvited' | 'error'> {
  const res = await authedFetchRaw('/conversations/current')
  if (res.ok) return 'ready'
  if (res.status === 403) return 'notInvited'
  return 'error'
}

export function getHistory(): Promise<HistoryItem[]> {
  return getJson<HistoryItem[]>('/history')
}

export function getRecommendations(): Promise<Recommendation[]> {
  return getJson<Recommendation[]>('/recommendations')
}

export async function setRecommendationStatus(id: string, status: 'Dismissed'): Promise<void> {
  const res = await authedFetchRaw(`/recommendations/${id}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  if (!res.ok) throw new Error(`dismiss → ${res.status}`)
}

export function getAnalysis(): Promise<Analysis> {
  return getJson<Analysis>('/analysis')
}

export function getCurrentConversation(): Promise<Conversation> {
  return getJson<Conversation>('/conversations/current')
}

export async function newConversation(): Promise<Conversation> {
  const res = await authedFetchRaw('/conversations', { method: 'POST' })
  if (!res.ok) throw new Error(`new conversation → ${res.status}`)
  return res.json() as Promise<Conversation>
}

export interface AddBookInput {
  title: string
  author: string
  format?: string
  rating?: number | null
  notes?: string | null
  date_completed?: string | null
}

export interface AddBookResult {
  work_id: string
  title: string
  read_number: number
  already_logged: boolean
  enrichment_enqueued: boolean
}

export async function addBook(input: AddBookInput): Promise<AddBookResult> {
  const res = await authedFetchRaw('/books', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!res.ok) throw new Error(`addBook → ${res.status}`)
  return res.json() as Promise<AddBookResult>
}

export interface ChatHandlers {
  onActivity: (kind: string, detail: string) => void
  onText: (text: string) => void
  onError: (detail: string) => void
  signal?: AbortSignal
}

const GENERIC_CHAT_ERROR = 'The Librarian hit a problem. Please try again.'

/** POST a chat turn and consume the SSE stream (EventSource cannot POST or set headers,
 *  so we use fetch + a streaming reader). Parses event frames split on a blank line and
 *  buffers across network chunk boundaries. Events: activity, text, error, done. */
export async function streamChat(message: string, handlers: ChatHandlers): Promise<void> {
  let res: Response
  try {
    res = await authedFetchRaw('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
      signal: handlers.signal,
    })
  } catch {
    handlers.onError(GENERIC_CHAT_ERROR)
    return
  }
  if (!res.ok || !res.body) {
    handlers.onError(GENERIC_CHAT_ERROR)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sep: number
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        dispatchFrame(frame, handlers)
      }
    }
  } catch {
    // A mid-stream read failure (e.g. the connection dropped) ends the turn as one error.
    handlers.onError(GENERIC_CHAT_ERROR)
  } finally {
    reader.releaseLock()
  }
}

function dispatchFrame(frame: string, handlers: ChatHandlers): void {
  let event = 'message'
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      // SSE joins multiple data: lines within one event with a newline.
      const content = line.slice(5).trim()
      data = data ? `${data}\n${content}` : content
    }
  }
  let payload: Record<string, string> = {}
  if (data) {
    try {
      payload = JSON.parse(data)
    } catch {
      return // ignore an unparseable frame rather than crash the stream
    }
  }
  if (event === 'activity') handlers.onActivity(payload.kind ?? '', payload.detail ?? '')
  else if (event === 'text') handlers.onText(payload.text ?? '')
  else if (event === 'error') handlers.onError(payload.detail ?? GENERIC_CHAT_ERROR)
  // 'done' → stream end, no callback
}
