import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { getRecommendations, getAvailability, setRecommendationStatus, type BookAvailability, type Recommendation } from '../api/client'
import BookLinks from '../components/BookLinks'
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
  // Recommendations are new-to-you by assumption, so a "New" (not-a-reread) badge is noise.
  // Only the meaningful "Re-read" case gets a badge.
  return null
}

export default function RecommendationsView() {
  const navigate = useNavigate()
  const [recs, setRecs] = useState<Recommendation[] | null>(null)
  const [avail, setAvail] = useState<Record<string, BookAvailability>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [newIds, setNewIds] = useState<Set<string>>(new Set())

  // Load once: measure "new since last visit" BEFORE marking these ids seen, freeze it in state,
  // then store the recs. A later dismiss only filters `recs` — `newIds` stays frozen, so the
  // remaining markers persist and the header count (derived below) simply decrements.
  useEffect(() => {
    void getRecommendations().then((data) => {
      const ids = data.map((r) => r.id)
      setNewIds(computeNewIds('recs', ids))
      markSeen('recs', ids)
      setRecs(data)
      const workIds = data.map((r) => r.work_id)
      if (workIds.length > 0) {
        void getAvailability(workIds).then(setAvail).catch(() => { /* links-only fallback: leave avail empty */ })
      }
    })
  }, [])

  const visibleNewCount = useMemo(() => (recs ? recs.filter((r) => newIds.has(r.id)).length : 0), [recs, newIds])

  async function resolve(id: string, status: 'Dismissed' | 'Removed') {
    setBusy(id)
    try {
      await setRecommendationStatus(id, status)
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
        {visibleNewCount > 0 && <span className="view-head__summary">{visibleNewCount} new</span>}
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
              <button className="btn btn--ghost" onClick={() => void resolve(r.id, 'Removed')} disabled={busy === r.id}>Not right now</button>
              <button className="btn btn--ghost" onClick={() => void resolve(r.id, 'Dismissed')} disabled={busy === r.id}>Not for me</button>
            </div>
            <BookLinks availability={avail[r.work_id]} />
          </article>
        ))}
      </div>
    </div>
  )
}
