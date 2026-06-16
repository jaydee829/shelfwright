import { useEffect, useState } from 'react'
import { getHistory, type HistoryItem } from '../api/client'
import './HistoryView.css'

const PAGE_SIZE = 50

export default function HistoryView() {
  const [items, setItems] = useState<HistoryItem[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)

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
            <div className="history-tropes">
              {h.tropes && h.tropes.length > 0 ? (
                <>
                  {h.genre && <span className="trope-chip genre">{h.genre}</span>}
                  {h.tropes.map((t) => (
                    <span key={t} className="trope-chip">{t}</span>
                  ))}
                </>
              ) : (
                <span className="trope-chip enriching">Enriching…</span>
              )}
            </div>
          </li>
        ))}
      </ul>
      {hasMore && (
        <button className="history-load-more" onClick={() => void loadMore()} disabled={loadingMore}>
          {loadingMore ? 'Loading…' : 'Load more'}
        </button>
      )}
    </div>
  )
}
