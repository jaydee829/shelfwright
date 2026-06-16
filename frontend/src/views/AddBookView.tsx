import { useState, type FormEvent } from 'react'
import { useLocation } from 'react-router'
import { addBook, setRecommendationStatus } from '../api/client'
import './AddBookView.css'

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

interface Prefill {
  title?: string
  author?: string
  suggestionId?: string
}

export default function AddBookView() {
  const prefill = (useLocation().state as Prefill | null) ?? {}
  const [title, setTitle] = useState(prefill.title ?? '')
  const [author, setAuthor] = useState(prefill.author ?? '')
  const [format, setFormat] = useState('ebook')
  const [rating, setRating] = useState('')
  const [notes, setNotes] = useState('')
  const [dateFinished, setDateFinished] = useState(today())
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const canSubmit = title.trim() !== '' && author.trim() !== '' && !busy

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setDone(null)
    try {
      const result = await addBook({
        title: title.trim(),
        author: author.trim(),
        format,
        rating: rating ? Number(rating) : null,
        notes: notes.trim() || null,
        date_completed: dateFinished || null,
      })
      // Came from a recommendation's "I read this" → close the loop.
      if (prefill.suggestionId) await setRecommendationStatus(prefill.suggestionId, 'Read')
      setDone(
        `Added "${result.title}"! Enriching in the background (~a minute) — its tropes will appear in your History.`,
      )
    } catch {
      setError("Couldn't add that book — check the title and author and try again.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="addbook">
      <h2>Add a book</h2>
      <form onSubmit={onSubmit} className="addbook-form">
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} required />
        </label>
        <label>
          Author
          <input value={author} onChange={(e) => setAuthor(e.target.value)} required />
        </label>
        <label>
          Format
          <select value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="ebook">ebook</option>
            <option value="audiobook">audiobook</option>
            <option value="paperback">paperback</option>
            <option value="hardcover">hardcover</option>
          </select>
        </label>
        <label>
          Rating
          <select value={rating} onChange={(e) => setRating(e.target.value)}>
            <option value="">—</option>
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>{'★'.repeat(n)}</option>
            ))}
          </select>
        </label>
        <label>
          Date finished
          <input type="date" value={dateFinished} onChange={(e) => setDateFinished(e.target.value)} />
        </label>
        <label>
          Notes
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
        </label>
        <button type="submit" disabled={!canSubmit}>Add to history</button>
      </form>
      {done && <p className="addbook-done">{done}</p>}
      {error && <p className="addbook-error">{error}</p>}
    </div>
  )
}
