import type { Ranked } from '../api/client'
import './WordCloud.css'

const MIN_PX = 13
const MAX_PX = 30

/** A frequency-sized word cloud. Size + weight encode count; color cycles the
 * categorical palette so every word — including the smallest — stays readable.
 * Shared by the trope cloud and the style cloud. */
export default function WordCloud({ items }: { items: Ranked[] }) {
  if (items.length === 0) return null
  const counts = items.map((i) => i.count)
  const lo = Math.min(...counts)
  const hi = Math.max(...counts)
  const size = (c: number) => (hi === lo ? (MIN_PX + MAX_PX) / 2 : MIN_PX + ((c - lo) / (hi - lo)) * (MAX_PX - MIN_PX))

  return (
    <ul className="word-cloud">
      {items.map((it, idx) => {
        const px = size(it.count)
        return (
          <li key={it.name}>
            <span
              style={{
                fontSize: `${px.toFixed(1)}px`,
                color: `var(--cat-${(idx % 6) + 1})`,
                fontWeight: px > 22 ? 600 : 400,
              }}
            >
              {it.name}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
