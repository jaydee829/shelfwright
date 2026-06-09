import { useAuth } from '../auth/AuthContext'

export default function TopBar() {
  const { user, signOut } = useAuth()
  const initial = (user?.displayName || user?.email || '?').charAt(0).toUpperCase()
  return (
    <header className="topbar">
      <span className="topbar-title">The Librarian</span>
      <div className="topbar-right">
        <span className="avatar" title={user?.email ?? ''}>{initial}</span>
        <button onClick={() => void signOut()}>Sign out</button>
      </div>
    </header>
  )
}
