import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { probeAccess } from '../api/client'
import { onAuth, signInWithGoogle, signOutUser } from './firebase'

export type AuthStatus = 'loading' | 'signedOut' | 'notInvited' | 'ready'

interface AuthUser {
  email: string | null
  displayName?: string | null
}

interface AuthValue {
  status: AuthStatus
  user: AuthUser | null
  signIn: () => Promise<unknown>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<AuthUser | null>(null)

  useEffect(() => {
    return onAuth(async (fbUser) => {
      if (!fbUser) {
        setUser(null)
        setStatus('signedOut')
        return
      }
      setUser({ email: fbUser.email, displayName: fbUser.displayName })
      setStatus('loading')
      // Firebase verified the identity; the backend decides invited-or-not (403).
      const access = await probeAccess()
      setStatus(access === 'ready' ? 'ready' : 'notInvited')
    })
  }, [])

  return (
    <AuthContext.Provider value={{ status, user, signIn: signInWithGoogle, signOut: signOutUser }}>
      {children}
    </AuthContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
