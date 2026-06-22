import { useAuth } from '../auth/AuthContext'
import './NotInvited.css'

export default function NotInvited() {
  const { user, signOut } = useAuth()
  return (
    <div className="notinvited-root">
      <div className="notinvited-card">
        <h1 className="notinvited-title">
          <span className="notinvited-gilt" aria-hidden="true">✦</span> You're not on the list yet
        </h1>
        <p className="notinvited-body">
          You're signed in as <strong>{user?.email}</strong>, but this account hasn't been invited.
          Ask the operator for an invite, then sign in again.
        </p>
        <button className="btn btn--ghost" onClick={() => void signOut()}>
          Sign out
        </button>
      </div>
    </div>
  )
}
