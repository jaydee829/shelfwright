import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer } from 'recharts'
import type { StyleAxis, StyleRadar as RadarData } from '../api/client'
import './StyleRadar.css'

const AXIS_LABEL: Record<StyleAxis, string> = {
  pace: 'Pace', density: 'Density', depth: 'Depth', inner_focus: 'Inner focus',
  humor: 'Humor', warmth: 'Warmth', lexicon: 'Lexicon', world_building: 'World-building',
}
const ORDER: StyleAxis[] = ['pace', 'density', 'depth', 'inner_focus', 'humor', 'warmth', 'lexicon', 'world_building']
const MIN_AXES = 3

export default function StyleRadar({ radar }: { radar?: RadarData }) {
  const points = radar
    ? ORDER.filter((a) => radar[a] !== null).map((a) => ({ axis: AXIS_LABEL[a], value: radar[a] as number }))
    : []

  if (points.length < MIN_AXES) {
    return <p className="radar-empty muted">Gathering your style… read a few more books and your shape will appear.</p>
  }

  const summary = 'The shape of your reading: ' + points.map((p) => `${p.axis} ${Math.round(p.value * 100)}%`).join(', ')

  return (
    <div className="style-radar" role="img" aria-label={summary}>
      <ResponsiveContainer width="100%" height={280}>
        <RadarChart data={points} outerRadius="72%">
          <PolarGrid className="radar-grid" />
          <PolarAngleAxis dataKey="axis" className="radar-axis" tick={{ fontSize: 12 }} />
          <PolarRadiusAxis domain={[0, 1]} tick={false} axisLine={false} />
          <Radar className="radar-shape" dataKey="value" fillOpacity={0.3} isAnimationActive={false} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}
