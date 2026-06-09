import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../auth/firebase', () => ({
  getIdToken: vi.fn(),
}))

import { getIdToken } from '../auth/firebase'
import { probeAccess, streamChat } from './client'

function sseStream(chunks: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder()
      for (const c of chunks) controller.enqueue(enc.encode(c))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

describe('api client', () => {
  beforeEach(() => {
    vi.mocked(getIdToken).mockResolvedValue('tok-123')
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => vi.unstubAllGlobals())

  it('attaches the bearer token on requests', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('ok', { status: 200 }))
    await probeAccess()
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init!.headers as Headers).get('Authorization')).toBe('Bearer tok-123')
  })

  it('probeAccess maps 200 → ready and 403 → notInvited', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('{}', { status: 200 }))
    expect(await probeAccess()).toBe('ready')
    vi.mocked(fetch).mockResolvedValueOnce(new Response('no', { status: 403 }))
    expect(await probeAccess()).toBe('notInvited')
  })

  it('streamChat parses activity, text, done across chunk boundaries', async () => {
    // The "text" event is split across two network chunks to prove buffering.
    vi.mocked(fetch).mockResolvedValue(
      sseStream([
        'event: activity\ndata: {"kind":"search","detail":"Explorer is searching"}\n\n',
        'event: text\ndata: {"text":"Hello',
        ' there"}\n\nevent: done\ndata: {}\n\n',
      ]),
    )
    const activity: string[] = []
    let text = ''
    let errored = false
    await streamChat('hi', {
      onActivity: (_k, d) => activity.push(d),
      onText: (t) => (text += t),
      onError: () => (errored = true),
    })
    expect(activity).toEqual(['Explorer is searching'])
    expect(text).toBe('Hello there')
    expect(errored).toBe(false)
  })

  it('streamChat reports a single error event', async () => {
    vi.mocked(fetch).mockResolvedValue(sseStream(['event: error\ndata: {"detail":"boom"}\n\n']))
    let detail = ''
    await streamChat('hi', { onActivity: () => {}, onText: () => {}, onError: (d) => (detail = d) })
    expect(detail).toBe('boom')
  })
})
