import { useState } from 'react'
import type { ActivityStep } from '../api/activityLabels'
import './ActivityTrail.css'

function StepRow({ step }: { step: ActivityStep }) {
  return (
    <div className={`trail-step ${step.stepKind}`}>
      <span className={`trail-mark ${step.status}`} aria-hidden>
        {step.status === 'done' ? '✓' : '⟳'}
      </span>
      <span className="trail-text">{step.text}</span>
    </div>
  )
}

/** In-flight trail: a live checklist that also serves as the pending indicator. */
export function LiveActivityTrail({ steps }: { steps: ActivityStep[] }) {
  if (steps.length === 0) return null
  return (
    <div className="activity-trail live" role="status" aria-live="polite">
      {steps.map((s) => (
        <StepRow key={s.id} step={s} />
      ))}
    </div>
  )
}

/** Completed trail on a finished assistant message: a collapsed disclosure above the reply. */
export function CompletedActivityTrail({ steps }: { steps: ActivityStep[] }) {
  const [open, setOpen] = useState(false)
  if (steps.length === 0) return null
  return (
    <div className="activity-trail done">
      <button className="trail-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? '▾' : '▸'} How I found these ({steps.length} step{steps.length === 1 ? '' : 's'})
      </button>
      {open && steps.map((s) => <StepRow key={s.id} step={s} />)}
    </div>
  )
}
