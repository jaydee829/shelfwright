import { useAuth } from '../auth/AuthContext'
import './SignIn.css'

export default function SignIn() {
  const { signIn } = useAuth()
  return (
    <div className="signin-root">
      <div className="signin-card">
        <h1 className="signin-title">
          <span className="signin-gilt" aria-hidden="true">✦</span> Shelfwright
        </h1>
        <p className="signin-subtitle">Your personal reading companion.</p>
        <button className="btn" onClick={() => void signIn()}>
          Sign in with Google
        </button>
      </div>
    </div>
  )
}
