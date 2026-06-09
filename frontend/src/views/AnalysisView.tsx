import { useEffect, useState } from 'react'
import { getAnalysis, type Analysis, type Ranked } from '../api/client'
import './AnalysisView.css'

type Tab = 'snapshot' | 'genres' | 'tropes' | 'people'

const TABS: { id: Tab; label: string }[] = [
  { id: 'snapshot', label: 'Snapshot' },
  { id: 'genres', label: 'Genre & mood' },
  { id: 'tropes', label: 'Top tropes' },
  { id: 'people', label: 'Authors & narrators' },
]

function RankedList({ title, items }: { title: string; items: Ranked[] }) {
  return (
    <div className="ranked">
      <h3>{title}</h3>
      {items.length === 0 ? (
        <p className="muted">No data yet.</p>
      ) : (
        <ul>
          {items.map((it) => (
            <li key={it.name}>
              <span>{it.name}</span>
              <span className="count">{it.count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function AnalysisView() {
  const [data, setData] = useState<Analysis | null>(null)
  const [tab, setTab] = useState<Tab>('snapshot')

  useEffect(() => {
    void getAnalysis().then(setData)
  }, [])

  if (data === null) return <p>Loading…</p>

  return (
    <div>
      <h2>Analysis</h2>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? 'tab active' : 'tab'}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'snapshot' && (
        <div className="snapshot-grid">
          <div className="stat"><span className="stat-num">{data.snapshot.total_read}</span><span>books read</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.read_this_year}</span><span>this year</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.average_rating ?? '—'}</span><span>avg rating</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.distinct_authors}</span><span>authors</span></div>
          <RankedList title="Formats" items={data.snapshot.formats} />
        </div>
      )}
      {tab === 'genres' && (
        <div className="two-col">
          <RankedList title="Genres" items={data.genres} />
          <RankedList title="Moods" items={data.moods} />
        </div>
      )}
      {tab === 'tropes' && <RankedList title="Top tropes" items={data.top_tropes} />}
      {tab === 'people' && (
        <div className="two-col">
          <RankedList title="Authors" items={data.authors} />
          <RankedList title="Narrators" items={data.narrators} />
        </div>
      )}
    </div>
  )
}
