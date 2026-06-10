import { useEffect, useState } from 'react'
import { getRecommendations, setRecommendationStatus, type Recommendation } from '../api/client'
import './RecommendationsView.css'

export default function RecommendationsView() {
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
            </div>
            {r.justification && <p className="rec-why">{r.justification}</p>}
            <div className="rec-actions">
              {/* "I read this" routes through the add-book form — wired in Stage 3. */}
              <button disabled title="Coming soon — adds the book to your history">✓ I read this</button>
              <button onClick={() => void dismiss(r.id)} disabled={busy === r.id}>Not for me</button>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
