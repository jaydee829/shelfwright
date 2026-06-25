import { Bar, BarChart, ResponsiveContainer, XAxis, YAxis } from 'recharts'
import type { Ranked } from '../api/client'
import './GenreMoodBars.css'

/** Horizontal bar chart for genres or moods. Single-hue gilt (row labels
 * differentiate). Exposes an accessible summary for tests + screen readers. */
export default function GenreMoodBars({ title, items }: { title: string; items: Ranked[] }) {
  if (items.length === 0) return <p className="muted">No data yet.</p>

  const summary = `${title}: ` + items.map((it) => `${it.name} ${it.count}`).join(', ')
  const height = Math.max(120, items.length * 34)

  return (
    <div className="genre-mood-bars" role="img" aria-label={summary}>
      <h3>{title}</h3>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={items} layout="vertical" margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
          <XAxis type="number" hide />
          <YAxis type="category" dataKey="name" width={120} className="bars-axis" tick={{ fontSize: 12 }} />
          <Bar className="bars-bar" dataKey="count" radius={[0, 4, 4, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
