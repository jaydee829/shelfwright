import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyTheme, getStoredTheme, resolveInitialTheme, setTheme } from './theme'

function mockMatchMedia(matches: boolean) {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches }))
}

afterEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  vi.unstubAllGlobals()
})

describe('theme', () => {
  it('resolves the stored theme when present', () => {
    localStorage.setItem('theme', 'dark')
    expect(resolveInitialTheme()).toBe('dark')
  })

  it('falls back to OS preference when nothing is stored', () => {
    mockMatchMedia(true)
    expect(resolveInitialTheme()).toBe('dark')
    mockMatchMedia(false)
    expect(resolveInitialTheme()).toBe('light')
  })

  it('ignores an invalid stored value', () => {
    localStorage.setItem('theme', 'rainbow')
    mockMatchMedia(false)
    expect(getStoredTheme()).toBeNull()
    expect(resolveInitialTheme()).toBe('light')
  })

  it('setTheme sets data-theme and persists', () => {
    setTheme('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem('theme')).toBe('dark')
  })

  it('setTheme does not throw when localStorage is unavailable', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied')
    })
    expect(() => setTheme('dark')).not.toThrow()
    expect(document.documentElement.dataset.theme).toBe('dark')
    spy.mockRestore()
  })

  it('applyTheme sets the attribute', () => {
    applyTheme('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})
