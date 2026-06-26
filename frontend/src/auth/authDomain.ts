export interface LocationLike {
  hostname: string
  host: string
}

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1'])

/**
 * Resolve the Firebase `authDomain`.
 *
 * In a browser on a real (non-localhost) host, Firebase's `/__/auth/*` helper is served
 * SAME-ORIGIN by our FastAPI proxy, so `authDomain` must be our own host — that makes the
 * helper's sessionStorage first-party and fixes Safari-mobile sign-in (GH #78). It also
 * follows any future custom domain automatically. Local dev / tests / non-browser have no
 * proxy, so fall back to the build-time configured Firebase domain.
 */
export function resolveAuthDomain(
  envDomain: string | undefined,
  loc: LocationLike | undefined,
): string | undefined {
  if (loc && !LOCAL_HOSTS.has(loc.hostname)) {
    return loc.host
  }
  return envDomain
}
