export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'theme'

function prefersDark(): boolean {
  try {
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches === true
  } catch {
    return false
  }
}

export function getStoredTheme(): Theme | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return v === 'light' || v === 'dark' ? v : null
  } catch {
    return null
  }
}

export function resolveInitialTheme(): Theme {
  return getStoredTheme() ?? (prefersDark() ? 'dark' : 'light')
}

export function applyTheme(t: Theme): void {
  document.documentElement.dataset.theme = t
}

export function setTheme(t: Theme): void {
  applyTheme(t)
  try {
    localStorage.setItem(STORAGE_KEY, t)
  } catch {
    /* storage unavailable (private mode) — theme still applies for the session */
  }
}
