import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatHotkey, HOTKEYS, HOTKEY_ROWS, matchesNewSessionHotkey } from './hotkeyCatalog'

afterEach(() => vi.unstubAllGlobals())

describe('platform shortcut contract', () => {
  it('lists implemented navigation shortcuts for both help surfaces', () => {
    const chords = HOTKEY_ROWS.map(row => row.keys)
    for (const chord of [HOTKEYS.conversationSearch, HOTKEYS.sessionSearch, HOTKEYS.git,
      HOTKEYS.browser, HOTKEYS.tools, HOTKEYS.todos, HOTKEYS.context]) {
      expect(chords.filter(value => value === chord)).toHaveLength(1)
    }
    expect(chords).not.toContain('Shift+Tab')
  })

  it('displays the primary modifier for each platform while retaining literal Control', () => {
    expect(formatHotkey(HOTKEYS.newSession, true)).toBe('⌘+Shift+U')
    expect(formatHotkey(HOTKEYS.newSession, false)).toBe('Ctrl+Shift+U')
    expect(formatHotkey(HOTKEYS.settings, true)).toBe('⌘+,')
    expect(formatHotkey(HOTKEYS.git, true)).toBe('⌘+Shift+G')
    expect(formatHotkey(HOTKEYS.terminal, true)).toBe('Ctrl+`')
    expect(formatHotkey(HOTKEYS.browser, true)).toBe('Ctrl+Shift+`')
    expect(HOTKEY_ROWS[0].keys).toBe(HOTKEYS.newSession)
  })

  for (const [platform, modifier, other] of [['MacIntel', 'metaKey', 'ctrlKey'],
                                            ['Win32', 'ctrlKey', 'metaKey'],
                                            ['Linux x86_64', 'ctrlKey', 'metaKey']] as const) {
    it(`requires the actual new-session chord on ${platform}`, () => {
      vi.stubGlobal('navigator', { platform })
      const base = { key: 'u', metaKey: false, ctrlKey: false, shiftKey: true, altKey: false }
      expect(matchesNewSessionHotkey({ ...base, [modifier]: true })).toBe(true)
      expect(matchesNewSessionHotkey({ ...base, key: 'U', [modifier]: true })).toBe(true)
      expect(matchesNewSessionHotkey(base)).toBe(false)
      expect(matchesNewSessionHotkey({ ...base, [other]: true })).toBe(false)
      expect(matchesNewSessionHotkey({ ...base, [modifier]: true, shiftKey: false })).toBe(false)
      expect(matchesNewSessionHotkey({ ...base, key: 'n', [modifier]: true, shiftKey: false })).toBe(false)
      for (const extra of ['altKey', other] as const) {
        expect(matchesNewSessionHotkey({ ...base, [modifier]: true, [extra]: true })).toBe(false)
      }
    })
  }
})
