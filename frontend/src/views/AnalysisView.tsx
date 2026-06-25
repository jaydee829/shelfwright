import { useEffect, useState } from 'react'
import GenreMoodBars from '../components/GenreMoodBars'
import ProportionBar from '../components/ProportionBar'
import StyleRadar from '../components/StyleRadar'
import WordCloud from '../components/WordCloud'
import { getAnalysis, type Analysis, type Ranked } from '../api/client'
import './AnalysisView.css'

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

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="analysis-section">
      <h2 className="section-title">{title}</h2>
      {children}
    </section>
  )
}

export default function AnalysisView() {
  const [data, setData] = useState<Analysis | null>(null)

  useEffect(() => {
    void getAnalysis().then(setData)
  }, [])

  if (data === null) return <p>Loading…</p>

  return (
    <div className="analysis">
      <h2>Analysis</h2>

      <Section title="Your reading">
        <div className="snapshot-grid">
          <div className="stat"><span className="stat-num">{data.snapshot.total_read}</span><span>books read</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.read_this_year}</span><span>this year</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.average_rating ?? '—'}</span><span>avg rating</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.distinct_authors}</span><span>authors</span></div>
        </div>
        <ProportionBar items={data.snapshot.formats} />
      </Section>

      <Section title="The shape of your reading">
        <StyleRadar radar={data.style_radar} />
      </Section>

      <Section title="Your signature tropes">
        <WordCloud items={data.top_tropes} />
      </Section>

      <Section title="Genre & mood">
        <div className="two-col">
          <GenreMoodBars title="Genres" items={data.genres} />
          <GenreMoodBars title="Moods" items={data.moods} />
        </div>
      </Section>

      {data.style_cloud && data.style_cloud.length > 0 && (
        <Section title="Your style">
          <WordCloud items={data.style_cloud} />
        </Section>
      )}

      <Section title="Authors & narrators">
        <div className="two-col">
          <RankedList title="Authors" items={data.authors} />
          <RankedList title="Narrators" items={data.narrators} />
        </div>
      </Section>
    </div>
  )
}
