import type { BookAvailability, LibbyFormat } from '../api/client'
import './BookLinks.css'

function formatLabel(f: LibbyFormat): string {
  if (f.available) return `${f.format} available now`
  if (f.wait_days && f.wait_days > 0) {
    const weeks = Math.round(f.wait_days / 7)
    return `${f.format} ~${weeks}wk wait`
  }
  return `${f.format} on hold`
}

export default function BookLinks({ availability }: { availability?: BookAvailability }) {
  if (!availability) return null
  const { links, libby } = availability
  return (
    <div className="book-links">
      {libby.length > 0 && (
        <ul className="book-links__avail">
          {libby.map((lib) => (
            <li key={lib.slug} className="book-links__lib">
              <span className="book-links__libname">{lib.library}</span>
              {lib.formats.map((f) => (
                <span key={f.format} className={`book-links__fmt${f.available ? ' is-available' : ''}`}>
                  {formatLabel(f)}
                </span>
              ))}
            </li>
          ))}
        </ul>
      )}
      <div className="book-links__row">
        {links.map((link) => (
          <a key={link.kind + link.url} className={`book-links__link kind-${link.kind}`}
             href={link.url} target="_blank" rel="noreferrer">
            {link.label}
          </a>
        ))}
      </div>
    </div>
  )
}
