import { initializeApp } from 'firebase/app'
import {
  GoogleAuthProvider,
  getAuth,
  getRedirectResult,
  onAuthStateChanged,
  signInWithPopup,
  signOut,
  type User,
} from 'firebase/auth'

import { resolveAuthDomain } from './authDomain'

const app = initializeApp({
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  // Same-origin authDomain on real hosts (served via the /__/auth proxy) fixes
  // Safari-mobile sign-in (GH #78); env fallback for local dev / tests.
  authDomain: resolveAuthDomain(
    import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
    typeof window !== 'undefined' ? window.location : undefined,
  ),
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
})

export const auth = getAuth(app)

// Safari/iOS may down-convert signInWithPopup to a redirect; complete any pending redirect
// on load. Harmless no-op (result === null) otherwise. This works now because the
// same-origin authDomain makes the helper's storage first-party (GH #78). Never throw into
// app load — sign-in errors surface via onAuthStateChanged / the sign-in UI.
void getRedirectResult(auth).catch(() => {})

export function onAuth(callback: (user: User | null) => void): () => void {
  return onAuthStateChanged(auth, callback)
}

export function signInWithGoogle(): Promise<unknown> {
  return signInWithPopup(auth, new GoogleAuthProvider())
}

export function signOutUser(): Promise<void> {
  return signOut(auth)
}

/** The current user's Firebase ID token, or null when signed out. The SDK auto-refreshes. */
export async function getIdToken(): Promise<string | null> {
  return auth.currentUser ? auth.currentUser.getIdToken() : null
}
