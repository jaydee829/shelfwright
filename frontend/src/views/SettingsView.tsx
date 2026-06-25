import { useEffect, useState } from 'react'
import { getMyLibraries, searchLibraries, saveMyLibraries, type SavedLibrary } from '../api/client'
import './SettingsView.css'

export default function SettingsView() {
  const [saved, setSaved] = useState<SavedLibrary[]>([])
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SavedLibrary[]>([])
  const [status, setStatus] = useState<string>('')

  useEffect(() => { void getMyLibraries().then(setSaved) }, [])

  useEffect(() => {
    const q = query.trim()
    const t = setTimeout(() => {
      if (!q) { setResults([]); return }
      void searchLibraries(q).then(setResults).catch(() => setResults([]))
    }, 300)
    return () => clearTimeout(t)
  }, [query])

  function add(lib: SavedLibrary) {
    if (!saved.some((s) => s.slug === lib.slug)) setSaved([...saved, lib])
  }
  function remove(slug: string) { setSaved(saved.filter((s) => s.slug !== slug)) }
  function move(i: number, delta: number) {
    const j = i + delta
    if (j < 0 || j >= saved.length) return
    const next = [...saved]
    ;[next[i], next[j]] = [next[j], next[i]]
    setSaved(next)
  }
  async function save() {
    setStatus('Saving…')
    try { await saveMyLibraries(saved); setStatus('Saved') } catch { setStatus('Save failed') }
  }

  return (
    <div className="settings">
      <header className="view-head"><h2>Libraries</h2></header>
      <p className="settings__hint">Search for the library systems you have a Libby card for. We'll show live
        availability for these on your recommendations, in your priority order.</p>

      <ul className="settings__saved">
        {saved.map((lib, i) => (
          <li key={lib.slug} className="settings__saved-row">
            <span>{lib.name}</span>
            <span className="settings__controls">
              <button className="btn btn--ghost" onClick={() => move(i, -1)} aria-label="Move up">↑</button>
              <button className="btn btn--ghost" onClick={() => move(i, 1)} aria-label="Move down">↓</button>
              <button className="btn btn--ghost" onClick={() => remove(lib.slug)}>Remove</button>
            </span>
          </li>
        ))}
      </ul>

      <input className="settings__search" placeholder="Search for your library…"
             value={query} onChange={(e) => setQuery(e.target.value)} />
      <ul className="settings__results">
        {results.map((lib) => (
          <li key={lib.slug} className="settings__result-row">
            <span>{lib.name}</span>
            <button className="btn" onClick={() => add(lib)}>Add</button>
          </li>
        ))}
      </ul>

      <div className="settings__actions">
        <button className="btn" onClick={() => void save()}>Save</button>
        {status && <span className="settings__status">{status}</span>}
      </div>
    </div>
  )
}
