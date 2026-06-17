import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { updateHistory, type HistoryItem } from '../api/client'
import './AddBookView.css'

export default function HistoryEditView() {
  const { id } = useParams()
  const navigate = useNavigate()
  const row = (useLocation().state as HistoryItem | null) ?? null
  const [rating, setRating] = useState(row?.rating != null ? String(row.rating) : '')
  const [dateFinished, setDateFinished] = useState(row?.date_completed ?? '')
  const [notes, setNotes] = useState(row?.notes ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!row || !id) return <Navigate to="/history" replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await updateHistory(id as string, {
        rating: rating ? Number(rating) : null,
        date_completed: dateFinished || null,
        notes: notes.trim() || null,
      })
      navigate('/history')
    } catch {
      setError("Couldn't save those changes — try again.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="addbook">
      <h2>Edit read</h2>
      <p className="edit-context">
        <strong>{row.title}</strong>
        <br />
        {row.authors.join(', ')}
        {row.format ? ` · ${row.format}` : ''}
      </p>
      <form onSubmit={onSubmit} className="addbook-form">
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
          <input type="date" value={dateFinished} onChange={(e) => setDateFinished(e.target.value)} required />
        </label>
        <label>
          Notes
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
        </label>
        <div className="edit-actions">
          <button type="submit" disabled={busy}>Save changes</button>
          <button type="button" onClick={() => navigate('/history')} disabled={busy}>Cancel</button>
        </div>
      </form>
      {error && <p className="addbook-error">{error}</p>}
    </div>
  )
}
