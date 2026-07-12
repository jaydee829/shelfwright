import { useState } from 'react'
import { useAuth } from '../auth/AuthContext'
import { setTheme, type Theme } from '../theme'

export default function TopBar() {
  const { user, signOut } = useAuth()
  const initial = (user?.displayName || user?.email || '?').charAt(0).toUpperCase()
  const [theme, setThemeState] = useState<Theme>((document.documentElement.dataset.theme as Theme) || 'light')

  function toggleTheme() {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    setThemeState(next)
  }

  return (
    <header className="topbar">
      <span className="topbar-title">Shelfwright</span>
      <div className="topbar-right">
        <button
          className="theme-toggle"
          onClick={toggleTheme}
          aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {theme === 'dark' ? '☀' : '🌙'}
        </button>
        <span className="avatar" title={user?.email ?? ''}>{initial}</span>
        <button onClick={() => void signOut()}>Sign out</button>
      </div>
    </header>
  )
}
