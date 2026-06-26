import { useEffect, useMemo, useRef, useState } from 'react'
import { useWordCloud } from '@isoterik/react-word-cloud'
import type { Ranked } from '../api/client'
import { prepareCloudWords } from './wordCloudText'
import { LARGE_PX, colorClass, mulberry32, rotateFor, sizeFor } from './wordCloudLayout'
import './WordCloud.css'

// d3-cloud measures glyphs on a canvas using this exact string before render.
// Keep in sync with `.word-cloud text { font-family }` (var(--font-display)) in WordCloud.css.
const FONT = "'Literata Variable', Georgia, serif"
const ASPECT = 0.6
const SEED = 1337
const DEFAULT_WIDTH = 600

/** A compact, rotated, frequency-accentuated word cloud. Runs the d3-cloud
 * "Wordle" layout via useWordCloud and renders the SVG <text> itself so color
 * (--cat-* palette), font, and light/dark theming stay under CSS control.
 * Shared by the trope cloud and the style cloud. */
export default function WordCloud({ items }: { items: Ranked[] }) {
  const words = useMemo(() => prepareCloudWords(items), [items])
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(DEFAULT_WIDTH)
  const random = useMemo(() => mulberry32(SEED), [])

  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w && w > 0) setWidth(w)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Spread is safe here: a cloud holds ~10-30 words (tropes/styles), never enough to overflow the call stack.
  const counts = words.map((w) => w.count)
  const lo = counts.length ? Math.min(...counts) : 0
  const hi = counts.length ? Math.max(...counts) : 0
  const height = Math.round(width * ASPECT)

  const { computedWords } = useWordCloud({
    words: words.map((w) => ({ text: w.name, value: w.count })),
    width,
    height,
    font: FONT,
    fontWeight: 'normal',
    fontStyle: 'normal',
    fontSize: (word) => sizeFor(word.value, lo, hi, width),
    rotate: (word) => rotateFor(word.text),
    padding: 1,
    spiral: 'archimedean',
    random,
  })

  if (words.length === 0) return null

  const colorByText = new Map(words.map((w, i) => [w.name, colorClass(i)]))
  const top = words.slice(0, 3).map((w) => w.name).join(', ')
  const more = words.length > 3 ? ` and ${words.length - 3} more` : ''
  const label = `Word cloud of ${words.length} word${words.length === 1 ? '' : 's'}. Most frequent: ${top}${more}.`

  return (
    <div className="word-cloud" ref={ref} role="img" aria-label={label}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
        <g transform={`translate(${width / 2},${height / 2})`}>
          {computedWords.map((w) => (
            <text
              key={w.text}
              className={`${colorByText.get(w.text) ?? 'cat-1'}${w.size >= LARGE_PX ? ' lg' : ''}`}
              textAnchor="middle"
              transform={`translate(${w.x},${w.y}) rotate(${w.rotate})`}
              style={{ fontSize: `${w.size}px`, fontFamily: FONT }}
            >
              {w.text}
            </text>
          ))}
        </g>
      </svg>
    </div>
  )
}
