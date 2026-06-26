import { describe, expect, it } from 'vitest'

import { resolveAuthDomain } from './authDomain'

describe('resolveAuthDomain', () => {
  it('uses the current host on a real (non-localhost) browser host', () => {
    expect(
      resolveAuthDomain('proj.firebaseapp.com', {
        hostname: 'librarian-api-abc.run.app',
        host: 'librarian-api-abc.run.app',
      }),
    ).toBe('librarian-api-abc.run.app')
  })

  it('preserves a port in the host', () => {
    expect(
      resolveAuthDomain('proj.firebaseapp.com', { hostname: 'app.example.com', host: 'app.example.com:8443' }),
    ).toBe('app.example.com:8443')
  })

  it('falls back to the configured domain on localhost', () => {
    expect(
      resolveAuthDomain('proj.firebaseapp.com', { hostname: 'localhost', host: 'localhost:5173' }),
    ).toBe('proj.firebaseapp.com')
  })

  it('falls back to the configured domain on 127.0.0.1', () => {
    expect(
      resolveAuthDomain('proj.firebaseapp.com', { hostname: '127.0.0.1', host: '127.0.0.1:5173' }),
    ).toBe('proj.firebaseapp.com')
  })

  it('falls back when there is no location (non-browser / SSR / test)', () => {
    expect(resolveAuthDomain('proj.firebaseapp.com', undefined)).toBe('proj.firebaseapp.com')
  })
})
