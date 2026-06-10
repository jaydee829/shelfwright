import { useEffect, useState } from 'react'
import { getHistory, type HistoryItem } from '../api/client'
import './HistoryView.css'

export default function HistoryView() {
  const [items, setItems] = useState<HistoryItem[] | null>(null)

  useEffect(() => {
    void getHistory().then(setItems)
  }, [])

  if (items === null) return <p>Loading…</p>
  if (items.length === 0) return <p>Nothing here yet — finish a book and it'll show up.</p>

  return (
    <div>
      <h2>Reading history</h2>
      <ul className="history-list">
        {items.map((h) => (
          <li key={h.id} className="history-row">
            <div className="history-main">
              <span className="history-title">{h.title}</span>
              <span className="history-authors">{h.authors.join(', ')}</span>
            </div>
            <div className="history-meta">
              {h.rating != null && <span className="history-rating">{'★'.repeat(h.rating)}</span>}
              {h.format && <span className="history-format">{h.format}</span>}
              {h.date_completed && <span className="history-date">{h.date_completed}</span>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
