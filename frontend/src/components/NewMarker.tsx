import './NewMarker.css'

const STAR = '<path d="M12 3 13.7 10.3 21 12 13.7 13.7 12 21 10.3 13.7 3 12 10.3 10.3Z"/>'

export function NewMarker({ kind }: { kind: 'new' | 'enriched' }) {
  const label = kind === 'new' ? 'New' : 'Enriched'
  return (
    <span className={`new-marker new-marker--${kind}`}>
      <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"
        aria-hidden="true" focusable="false" dangerouslySetInnerHTML={{ __html: STAR }} />
      <span className="new-marker__label">{label}</span>
    </span>
  )
}
