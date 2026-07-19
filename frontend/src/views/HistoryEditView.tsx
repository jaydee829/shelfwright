import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { ApiError, updateHistory, type HistoryItem } from '../api/client'
import './HistoryEditView.css'

export default function HistoryEditView() {
  const { id } = useParams()
  const navigate = useNavigate()
  const row = (useLocation().state as HistoryItem | null) ?? null
  const [rating, setRating] = useState(row?.rating != null ? String(row.rating) : '')
  const [dateFinished, setDateFinished] = useState(row?.date_completed ?? '')
  const [notes, setNotes] = useState(row?.notes ?? '')
  const [format, setFormat] = useState(row?.format ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!row || !id) return <Navigate to="/history" replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const body: Parameters<typeof updateHistory>[1] = {
        rating: rating ? Number(rating) : null,
        date_completed: dateFinished || null,
        notes: notes.trim() || null,
      }
      if (format && format !== row!.format) body.format = format
      await updateHistory(id as string, body)
      navigate('/history')
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setError(err.detail)
      else setError("Couldn't save those changes — try again.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="history-edit">
      <h2>Edit read</h2>
      <p className="history-edit-context">
        <strong>{row.title}</strong>
        <br />
        {row.authors.join(', ')}
        {row.format ? ` · ${row.format}` : ''}
      </p>
      <form onSubmit={onSubmit} className="history-edit-form">
        <label>
          Format
          <select value={format} onChange={(e) => setFormat(e.target.value)}>
            {!row.format && <option value="">—</option>}
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
          <input
            type="date"
            value={dateFinished}
            onChange={(e) => setDateFinished(e.target.value)}
            max={new Date().toLocaleDateString('en-CA')}
            required
          />
        </label>
        <label>
          Notes
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
        </label>
        <div className="history-edit-actions">
          <button type="submit" className="btn" disabled={busy}>Save changes</button>
          <button type="button" className="btn btn--ghost" onClick={() => navigate('/history')} disabled={busy}>Cancel</button>
        </div>
      </form>
      {error && <p className="history-edit-error">{error}</p>}
    </div>
  )
}
