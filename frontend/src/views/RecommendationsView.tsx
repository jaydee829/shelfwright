import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { getRecommendations, setRecommendationStatus, type Recommendation } from '../api/client'
import './RecommendationsView.css'

function ReadBadge({ r }: { r: Recommendation }) {
  if (r.read_status === 'reread') {
    const year = r.last_read ? new Date(r.last_read).getFullYear() : null
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

  useEffect(() => {
    void getRecommendations().then(setRecs)
  }, [])

  async function dismiss(id: string) {
    setBusy(id)
    try {
      await setRecommendationStatus(id, 'Dismissed')
      setRecs((current) => (current ? current.filter((r) => r.id !== id) : current))
    } finally {
      setBusy(null)
    }
  }

  function readThis(r: Recommendation) {
    // Open the add-book form prefilled; on a successful add it marks this suggestion Read.
    navigate('/add', { state: { title: r.title, author: r.authors.join(', '), suggestionId: r.id } })
  }

  if (recs === null) return <p>Loading…</p>
  if (recs.length === 0) return <p>No recommendations right now — ask the Librarian in Chat for ideas.</p>

  return (
    <div>
      <h2>Recommendations</h2>
      <div className="rec-list">
        {recs.map((r) => (
          <article key={r.id} className="rec-card">
            <div className="rec-head">
              <span className="rec-title">{r.title}</span>
              <span className="rec-authors">{r.authors.join(', ')}</span>
              <ReadBadge r={r} />
            </div>
            {r.justification && <p className="rec-why">{r.justification}</p>}
            <div className="rec-actions">
              <button onClick={() => readThis(r)}>✓ I read this</button>
              <button onClick={() => void dismiss(r.id)} disabled={busy === r.id}>Not for me</button>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
