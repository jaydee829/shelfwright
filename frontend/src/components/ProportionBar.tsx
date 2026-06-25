import type { Ranked } from '../api/client'
import './ProportionBar.css'

const INLINE_LABEL_MIN_PCT = 12 // show the label inside the segment above this width

/** A single horizontal 100%-stacked proportion bar: segments ordered largest ->
 * smallest, categorical colors, legend below as the source of truth. */
export default function ProportionBar({ items }: { items: Ranked[] }) {
  if (items.length === 0) return null
  const total = items.reduce((sum, it) => sum + it.count, 0) || 1
  const sorted = [...items].sort((a, b) => b.count - a.count)
  const pct = (c: number) => (c / total) * 100

  return (
    <div className="proportion">
      <div className="proportion-bar" role="img" aria-label={
        'Format mix: ' + sorted.map((it) => `${it.name} ${Math.round(pct(it.count))}%`).join(', ')
      }>
        {sorted.map((it, idx) => {
          const p = pct(it.count)
          return (
            <div
              key={it.name}
              data-testid="segment"
              className="proportion-seg"
              style={{ width: `${p}%`, background: `var(--cat-${(idx % 6) + 1})` }}
            >
              {p >= INLINE_LABEL_MIN_PCT && <span>{it.name} {Math.round(p)}%</span>}
            </div>
          )
        })}
      </div>
      <ul className="proportion-legend" aria-label="format legend">
        {sorted.map((it, idx) => (
          <li key={it.name}>
            <span className="swatch" style={{ background: `var(--cat-${(idx % 6) + 1})` }} aria-hidden="true" />
            {it.name} · {Math.round(pct(it.count))}%
          </li>
        ))}
      </ul>
    </div>
  )
}
