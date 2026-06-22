import { useEffect, useRef, useState, type ReactNode } from 'react'
import { getCurrentConversation, newConversation, streamChat, type ChatMessage } from '../api/client'
import { labelForActivity, type ActivityStep } from '../api/activityLabels'
import { CompletedActivityTrail, LiveActivityTrail } from './ActivityTrail'
import './ChatView.css'

// Minimal, dependency-free inline markdown for assistant replies: **bold**, *italic* / _italic_,
// and line breaks. Builds React elements (never innerHTML), so there is no injection surface.
function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = []
  // `_italic_` only at word boundaries, so snake_case (e.g. search_internal_database) isn't italicised.
  const re = /(\*\*[^*\n]+\*\*|\*[^*\n]+\*|(?<![A-Za-z0-9])_[^_\n]+_(?![A-Za-z0-9]))/g
  let last = 0
  let k = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('**')) out.push(<strong key={k++}>{tok.slice(2, -2)}</strong>)
    else out.push(<em key={k++}>{tok.slice(1, -1)}</em>)
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

function FormattedText({ text }: { text: string }) {
  return (
    <>
      {text.split('\n').map((line, i) => (
        <span key={i}>
          {i > 0 && <br />}
          {renderInline(line)}
        </span>
      ))}
    </>
  )
}

export default function ChatView() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [liveSteps, setLiveSteps] = useState<ActivityStep[]>([])
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const stepId = useRef(0)

  useEffect(() => {
    void getCurrentConversation().then((c) => setMessages(c.messages))
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [messages, liveSteps])

  async function send() {
    const text = input.trim()
    if (!text || sending) return
    setInput('')
    setSending(true)
    setLiveSteps([])
    stepId.current = 0
    let steps: ActivityStep[] = []
    let lastDetail = ''
    // Append the user turn plus an in-flight assistant placeholder (empty content; not rendered
    // as a bubble until text arrives — the live trail is the pending indicator).
    setMessages((m) => [...m, { role: 'user', content: text }, { role: 'assistant', content: '' }])
    let reply = ''
    await streamChat(text, {
      onActivity: (kind, detail) => {
        const label = labelForActivity(kind, detail)
        if (!label) return
        // Dedupe on the stable detail key, NOT the phrase: labelForActivity picks a random
        // phrase each call, so consecutive same-stage events would otherwise read as distinct.
        const normalizedDetail = (detail || '').toLowerCase()
        if (normalizedDetail === lastDetail) return
        lastDetail = normalizedDetail
        const prev = steps[steps.length - 1]
        if (prev && prev.status === 'running') {
          steps = [
            ...steps.slice(0, -1),
            { ...prev, status: 'done' },
            { id: ++stepId.current, text: label.text, stepKind: label.stepKind, status: 'running' },
          ]
        } else {
          steps = [...steps, { id: ++stepId.current, text: label.text, stepKind: label.stepKind, status: 'running' }]
        }
        setLiveSteps(steps)
      },
      onText: (chunk) => {
        reply += chunk
        setMessages((m) => [...m.slice(0, -1), { role: 'assistant', content: reply }])
      },
      onError: (detail) => {
        reply = reply || detail
        setMessages((m) => [...m.slice(0, -1), { role: 'assistant', content: reply }])
      },
    })
    // Finalize: mark the last running step done and attach the trail to the assistant message.
    steps = steps.map((s) => (s.status === 'running' ? { ...s, status: 'done' } : s))
    setMessages((m) => {
      const copy = [...m]
      const last = copy[copy.length - 1]
      if (last && last.role === 'assistant') copy[copy.length - 1] = { ...last, steps }
      return copy
    })
    setLiveSteps([])
    setSending(false)
  }

  async function startNew() {
    const c = await newConversation()
    setMessages(c.messages)
    setLiveSteps([])
  }

  return (
    <div className="chat">
      <div className="chat-toolbar">
        <button className="btn btn--ghost" onClick={() => void startNew()} disabled={sending}>New chat</button>
      </div>
      <div className="chat-thread">
        {messages.map((m, i) => (
          <div key={i} className="msg-row">
            {m.role === 'assistant' && m.steps && m.steps.length > 0 && <CompletedActivityTrail steps={m.steps} />}
            {(m.content || m.role === 'user') && (
              <div className={`bubble ${m.role}`}>
                {m.role === 'assistant' ? <FormattedText text={m.content} /> : m.content}
              </div>
            )}
          </div>
        ))}
        {sending && <LiveActivityTrail steps={liveSteps} />}
        <div ref={bottomRef} />
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault()
          void send()
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask the Librarian…"
          aria-label="Message"
        />
        <button className="btn" type="submit" disabled={sending}>Send</button>
      </form>
    </div>
  )
}
