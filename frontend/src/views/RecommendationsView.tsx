import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { getRecommendations, setRecommendationStatus, type Recommendation } from '../api/client'
import { GenreIcon } from '../components/GenreIcon'
import { NewMarker } from '../components/NewMarker'
import { computeNewIds, markSeen } from '../lib/lastVisit'
import './RecommendationsView.css'

function ReadBadge({ r }: { r: Recommendation }) {
  if (r.read_status === 'reread') {
    const year = r.last_read ? r.last_read.slice(0, 4) : null
    const stars = r.rating ? ` · ${'★'.repeat(r.rating)}` : ''
    return <span className="rec-badge reread">{year ? `Re-read · ${year}${stars}` : `Re-read${stars}`}</span>
  }
  if (r.read_status === 'new') return <span className="rec-badge new">New</span>
  return null
}

export default function RecommendationsView() {
  const navigate = useNavigate()
  const [recs, setRecs] = useState<Recommendation[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const newIds = useMemo(() => (recs ? computeNewIds('recs', recs.map((r) => r.id)) : new Set<string>()), [recs])

  useEffect(() => { void getRecommendations().then(setRecs) }, [])
  useEffect(() => { if (recs) markSeen('recs', recs.map((r) => r.id)) }, [recs])

  async function dismiss(id: string) {
    setBusy(id)
    try {
      await setRecommendationStatus(id, 'Dismissed')
      setRecs((cur) => (cur ? cur.filter((r) => r.id !== id) : cur))
    } finally { setBusy(null) }
  }
  function readThis(r: Recommendation) {
    navigate('/add', { state: { title: r.title, author: r.authors.join(', '), suggestionId: r.id } })
  }

  if (recs === null) return <p>Loading…</p>
  if (recs.length === 0) return <p>No recommendations right now — ask the Librarian in Chat for ideas.</p>

  return (
    <div>
      <header className="view-head">
        <h2>Recommendations</h2>
        {newIds.size > 0 && <span className="view-head__summary">{newIds.size} new</span>}
      </header>
      <div className="rec-list">
        {recs.map((r) => (
          <article key={r.id} className="book-card rec-card">
            <GenreIcon className="rec-genre" genres={r.genres} />
            {newIds.has(r.id) && <NewMarker kind="new" />}
            <div className="rec-head">
              <span className="rec-title">{r.title}</span>
              <span className="rec-authors">{r.authors.join(', ')}</span>
              <ReadBadge r={r} />
            </div>
            {r.justification && <p className="rec-why">{r.justification}</p>}
            <div className="rec-actions">
              <button className="btn" onClick={() => readThis(r)}>✓ I read this</button>
              <button className="btn btn--ghost" onClick={() => void dismiss(r.id)} disabled={busy === r.id}>Not for me</button>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
