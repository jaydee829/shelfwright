import { useEffect, useRef, useState } from 'react'
import { getCurrentConversation, newConversation, streamChat, type ChatMessage } from '../api/client'
import './ChatView.css'

export default function ChatView() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [activity, setActivity] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void getCurrentConversation().then((c) => setMessages(c.messages))
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [messages, activity])

  async function send() {
    const text = input.trim()
    if (!text || sending) return
    setInput('')
    setSending(true)
    setActivity(null)
    setMessages((m) => [...m, { role: 'user', content: text }])
    let reply = ''
    await streamChat(text, {
      onActivity: (_kind, detail) => setActivity(detail),
      onText: (chunk) => {
        reply += chunk
      },
      onError: (detail) => {
        reply = reply || detail
      },
    })
    setActivity(null)
    setMessages((m) => [...m, { role: 'assistant', content: reply }])
    setSending(false)
  }

  async function startNew() {
    const c = await newConversation()
    setMessages(c.messages)
    setActivity(null)
  }

  return (
    <div className="chat">
      <div className="chat-toolbar">
        <button onClick={() => void startNew()} disabled={sending}>New chat</button>
      </div>
      <div className="chat-thread">
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>{m.content}</div>
        ))}
        {activity && <div className="activity-chip">{activity}…</div>}
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
        <button type="submit" disabled={sending}>Send</button>
      </form>
    </div>
  )
}
