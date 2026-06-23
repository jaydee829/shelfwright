import type { ReactNode } from 'react'

export type LineIconName =
  | 'chat' | 'history' | 'picks' | 'analysis' | 'add' | 'import' | 'shelf'

const PATHS: Record<LineIconName, ReactNode> = {
  chat: <path d="M5 6 h14 a2 2 0 0 1 2 2 v6 a2 2 0 0 1 -2 2 h-9 l-4 3 v-3 H5 a2 2 0 0 1 -2 -2 V8 a2 2 0 0 1 2 -2 z" />,
  history: (
    <>
      <path d="M12 7 c-2 -1.3 -4.6 -1.5 -7 -1 v10 c2.4 -.5 5 -.3 7 1 c2 -1.3 4.6 -1.5 7 -1 V6 c-2.4 -.5 -5 -.3 -7 1 z" />
      <path d="M12 7 V18" />
    </>
  ),
  picks: <path d="M12 6 c.55 4.2 1.85 5.5 6 6 c-4.15 .5 -5.45 1.8 -6 6 c-.55 -4.2 -1.85 -5.5 -6 -6 c4.15 -.5 5.45 -1.8 6 -6 z" />,
  analysis: (
    <>
      <line x1="5" y1="18" x2="19" y2="18" />
      <line x1="7.5" y1="18" x2="7.5" y2="11" />
      <line x1="12" y1="18" x2="12" y2="6" />
      <line x1="16.5" y1="18" x2="16.5" y2="9" />
    </>
  ),
  add: (
    <>
      <circle cx="12" cy="12" r="7.5" />
      <line x1="12" y1="8" x2="12" y2="16" />
      <line x1="8" y1="12" x2="16" y2="12" />
    </>
  ),
  import: (
    <>
      <path d="M5 13 v5 a1 1 0 0 0 1 1 h12 a1 1 0 0 0 1 -1 v-5" />
      <path d="M12 4 V14" />
      <path d="M8.5 10.5 L12 14 L15.5 10.5" />
    </>
  ),
  shelf: (
    <>
      <rect x="3.5" y="6" width="4.5" height="13" rx="1" />
      <rect x="8.8" y="6" width="4.5" height="13" rx="1" />
      <g transform="rotate(-20 17.75 19)">
        <rect x="17.75" y="6" width="4.5" height="13" rx="1" />
      </g>
      <line x1="2.5" y1="20" x2="22.5" y2="20" />
    </>
  ),
}

export default function LineIcon({
  name,
  size = 24,
  className,
}: {
  name: LineIconName
  size?: number
  className?: string
}) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  )
}
