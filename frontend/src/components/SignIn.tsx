import { useAuth } from '../auth/AuthContext'

export default function SignIn() {
  const { signIn } = useAuth()
  return (
    <div style={{ display: 'grid', placeItems: 'center', minHeight: '100vh', textAlign: 'center', padding: 24 }}>
      <div>
        <h1>The Librarian</h1>
        <p>Your personal reading companion.</p>
        <button onClick={() => void signIn()} style={{ padding: '10px 20px', fontSize: 16 }}>
          Sign in with Google
        </button>
      </div>
    </div>
  )
}
