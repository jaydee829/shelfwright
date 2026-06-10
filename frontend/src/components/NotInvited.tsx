import { useAuth } from '../auth/AuthContext'

export default function NotInvited() {
  const { user, signOut } = useAuth()
  return (
    <div style={{ display: 'grid', placeItems: 'center', minHeight: '100vh', textAlign: 'center', padding: 24 }}>
      <div>
        <h1>You're not on the list yet</h1>
        <p>
          You're signed in as <strong>{user?.email}</strong>, but this account hasn't been invited.
          Ask the operator for an invite, then sign in again.
        </p>
        <button onClick={() => void signOut()} style={{ padding: '8px 16px' }}>
          Sign out
        </button>
      </div>
    </div>
  )
}
