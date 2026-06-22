import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { deleteHistory, getHistory, type HistoryItem } from '../api/client'
import './HistoryView.css'

const PAGE_SIZE = 50

export default function HistoryView() {
  const navigate = useNavigate()
  const [items, setItems] = useState<HistoryItem[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<HistoryItem | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void getHistory(PAGE_SIZE, 0).then((page) => {
      setItems(page)
      setHasMore(page.length === PAGE_SIZE)
    })
  }, [])

  async function loadMore() {
    if (items === null) return
    setLoadingMore(true)
    try {
      const page = await getHistory(PAGE_SIZE, items.length)
      setItems([...items, ...page])
      setHasMore(page.length === PAGE_SIZE)
    } finally {
      setLoadingMore(false)
    }
  }

  async function doDelete() {
    if (!confirm) return
    setDeleting(true)
    setError(null)
    try {
      await deleteHistory(confirm.id)
      setItems((cur) => (cur ? cur.filter((h) => h.id !== confirm.id) : cur))
      setConfirm(null)
    } catch {
      setError("Couldn't delete that entry — try again.")
    } finally {
      setDeleting(false)
    }
  }

  if (items === null) return <p>Loading…</p>
  if (items.length === 0) return <p>Nothing here yet — finish a book and it'll show up.</p>

  return (
    <div>
      <header className="view-head">
        <h2>Reading history</h2>
        <Link to="/import" className="history-import-link">Import history</Link>
      </header>
      <ul className="history-list">
        {items.map((h) => (
          <li key={h.id} className="book-card history-row">
            <div className="history-main">
              <span className="history-title">{h.title}</span>
              <span className="history-authors">{h.authors.join(', ')}</span>
            </div>
            <div className="history-meta">
              {h.rating != null && <span className="history-rating">{'★'.repeat(h.rating)}</span>}
              {h.format && <span className="history-format">{h.format}</span>}
              {h.date_completed && <span className="history-date">{h.date_completed}</span>}
            </div>
            <div className="history-tropes">
              {h.tropes && h.tropes.length > 0 ? (
                <>
                  {h.genre && <span className="chip history-genre">{h.genre}</span>}
                  {h.tropes.map((t) => (
                    <span key={t} className="chip">{t}</span>
                  ))}
                </>
              ) : (
                <span className="history-enriching">Enriching…</span>
              )}
            </div>
            <div className="history-actions">
              <button
                className="kebab"
                aria-label={`Actions for ${h.title}`}
                aria-haspopup="menu"
                onClick={() => setMenuFor(menuFor === h.id ? null : h.id)}
              >
                ⋮
              </button>
              {menuFor === h.id && (
                <div className="row-menu" role="menu">
                  <button
                    onClick={() => {
                      setMenuFor(null)
                      navigate(`/history/${h.id}/edit`, { state: h })
                    }}
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => {
                      setMenuFor(null)
                      setConfirm(h)
                    }}
                  >
                    Delete
                  </button>
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
      {hasMore && (
        <button className="btn btn--ghost history-load-more" onClick={() => void loadMore()} disabled={loadingMore}>
          {loadingMore ? 'Loading…' : 'Load more'}
        </button>
      )}
      {confirm && (
        <div className="confirm-backdrop" role="dialog" aria-modal="true" aria-label="Confirm delete">
          <div className="confirm-box">
            <p>
              Delete your read of "{confirm.title}"
              {confirm.date_completed ? ` finished ${confirm.date_completed}` : ''}? This can't be undone.
            </p>
            {error && <p className="confirm-error">{error}</p>}
            <div className="confirm-actions">
              <button
                className="btn btn--ghost"
                onClick={() => {
                  setConfirm(null)
                  setError(null)
                }}
                disabled={deleting}
              >
                Cancel
              </button>
              <button className="btn history-btn-danger" onClick={() => void doDelete()} disabled={deleting}>
                {deleting ? 'Deleting…' : 'Delete entry'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
