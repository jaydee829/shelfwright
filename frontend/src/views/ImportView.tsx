import { useEffect, useRef, useState } from 'react'
import {
  commitImport, getImportJob, previewImport, retryImport,
  type ColumnMapping, type ImportPreview, type ImportStatus,
} from '../api/client'
import './ImportView.css'

type Step = 'upload' | 'map' | 'review' | 'progress'
const FIELDS: Array<keyof ColumnMapping> = ['title', 'author', 'format', 'date_completed', 'rating', 'notes', 'shelf']
const REQUIRED: Array<keyof ColumnMapping> = ['title', 'author', 'date_completed']

export default function ImportView() {
  const [step, setStep] = useState<Step>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [mapping, setMapping] = useState<ColumnMapping>({})
  const [toRead, setToRead] = useState(true)
  const [currently, setCurrently] = useState(true)
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<ImportStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [pollKey, setPollKey] = useState(0)

  async function onFile(f: File) {
    setFile(f)
    setBusy(true)
    setError(null)
    try {
      const p = await previewImport(f)
      setPreview(p)
      setMapping(p.suggested_mapping)
      setStep('map')
    } catch {
      setError('Could not read that file. Make sure it is a CSV with a header row.')
    } finally {
      setBusy(false)
    }
  }

  const missing = REQUIRED.filter((f) => !mapping[f])

  async function onCommit() {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const res = await commitImport(file, mapping, { importToRead: toRead, importCurrentlyReading: currently })
      setJobId(res.import_job_id)
      setStep('progress')
    } catch {
      setError('Import could not start. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  async function onRetry() {
    if (!jobId || busy) return
    setBusy(true)
    setError(null)
    try {
      await retryImport(jobId)
      setStatus(null)
      setPollKey((k) => k + 1) // restart polling so progress reappears after retry
    } catch {
      setError('Retry could not start. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const timer = useRef<number | null>(null)
  useEffect(() => {
    if (step !== 'progress' || !jobId) return
    let active = true
    async function tick() {
      try {
        const s = await getImportJob(jobId!)
        if (!active) return
        setStatus(s)
        if (!s.complete) timer.current = window.setTimeout(tick, 2000)
      } catch {
        if (active) timer.current = window.setTimeout(tick, 4000)
      }
    }
    tick()
    return () => {
      active = false
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [step, jobId, pollKey])

  function downloadReport() {
    if (!status) return
    const header = 'title,author,status,outcome,skip_reason,error\n'
    const body = status.report
      .map((r) => [r.title, r.author, r.status, r.outcome, r.skip_reason, r.error]
        .map((v) => `"${(v ?? '').toString().replace(/"/g, '""')}"`).join(','))
      .join('\n')
    const url = URL.createObjectURL(new Blob([header + body], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'import-report.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const done = (status?.counts.done ?? 0) + (status?.counts.failed ?? 0) + (status?.counts.skipped ?? 0)

  return (
    <div className="import">
      <h2>Import reading history</h2>
      {error && <p className="import-error">{error}</p>}

      {step === 'upload' && (
        <div className="import-step">
          <p>Upload a CSV — a Goodreads export, or your own with title, author and date columns.</p>
          <label>
            Choose CSV file
            <input
              data-testid="import-file"
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
            />
          </label>
        </div>
      )}

      {step === 'map' && preview && (
        <div className="import-step">
          <p>Detected: {preview.source}</p>
          <div className="import-counts">
            <span>{preview.counts.read_dated} read</span>
            <span>{preview.counts.to_read} to-read</span>
            <span>{preview.counts.currently_reading} currently-reading</span>
          </div>
          {FIELDS.map((field) => (
            <label key={field} style={{ display: 'block' }}>
              {field}
              <select
                value={mapping[field] ?? ''}
                onChange={(e) => setMapping({ ...mapping, [field]: e.target.value || null })}
              >
                <option value="">—</option>
                {preview.headers.map((h) => <option key={h} value={h}>{h}</option>)}
              </select>
            </label>
          ))}
          <button disabled={missing.length > 0 || busy} onClick={() => setStep('review')}>Continue</button>
          {missing.length > 0 && <p className="import-error">Map required columns: {missing.join(', ')}</p>}
        </div>
      )}

      {step === 'review' && preview && (
        <div className="import-step">
          <p>{preview.counts.read_dated} books will be added to your history.</p>
          <label>
            <input type="checkbox" checked={toRead} onChange={(e) => setToRead(e.target.checked)} />
            Import {preview.counts.to_read} to-read books as wishlist
          </label>
          <label>
            <input type="checkbox" checked={currently} onChange={(e) => setCurrently(e.target.checked)} />
            Import {preview.counts.currently_reading} currently-reading as wishlist
          </label>
          <button disabled={busy} onClick={onCommit}>Start import</button>
        </div>
      )}

      {step === 'progress' && (
        <div className="import-step">
          <div className="import-progress-bar">
            <span style={{ width: `${status && status.total_rows ? (done / status.total_rows) * 100 : 0}%` }} />
          </div>
          <p>{done} / {status?.total_rows ?? '…'}</p>
          {status && (
            <ul>
              <li>✓ {status.counts.done ?? 0} imported</li>
              <li>⚠ {status.counts.failed ?? 0} failed</li>
              <li>⏭ {status.counts.skipped ?? 0} skipped</li>
            </ul>
          )}
          {status?.complete && (
            <>
              {status.report.length > 0 && (
                <button onClick={downloadReport}>Download report</button>
              )}
              {(status.counts.failed ?? 0) > 0 && (
                <button disabled={busy} onClick={onRetry}>Retry failed</button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
