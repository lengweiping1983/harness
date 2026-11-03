import {afterEach, expect, it, vi} from 'vitest'
import {DEFAULT_VOICE_SHORTCUT, formatVoiceShortcut, voiceShortcutAccelerator, recordedVoiceShortcut} from '../desktopVoice'

afterEach(() => vi.unstubAllGlobals())

it.each([
  ['MacIntel', '⌃ + ⌥ + Space', '⌘ + ⇧ + K'],
  ['Win32', 'Ctrl + Alt + Space', 'Ctrl + Shift + K'],
])('formats dictation shortcuts for %s and preserves their Electron meaning', (platform, shortcut, primary) => {
  vi.stubGlobal('navigator', {platform})
  expect(formatVoiceShortcut(DEFAULT_VOICE_SHORTCUT)).toBe(shortcut)
  expect(formatVoiceShortcut('CommandOrControl+Shift+K')).toBe(primary)
  expect(voiceShortcutAccelerator(shortcut)).toBe(DEFAULT_VOICE_SHORTCUT)
})

it.each([
  ['Control+Option+Space', 'Control+Alt+Space'],
  ['Ctrl + Alt + Space', 'Control+Alt+Space'],
  ['⌘ + ⇧ + K', 'Command+Shift+K'],
  ['CommandOrControl+Shift+F12', 'CommandOrControl+Shift+F12'],
  ['Unknown+K', 'Unknown+K'],
  ['constructor+K', 'constructor+K'],
])('converts explicit display aliases in %s without guessing other key names', (display, accelerator) => {
  expect(voiceShortcutAccelerator(display)).toBe(accelerator)
})

it('records physical letter, function and space chords while keeping Control literal on macOS', () => {
  const key={code:'KeyD',key:'d',ctrlKey:true,altKey:false,shiftKey:true,metaKey:false,repeat:false,isComposing:false,keyCode:68}
  expect(recordedVoiceShortcut(key)).toBe('Control+Shift+D')
  expect(recordedVoiceShortcut({...key,code:'KeyD',key:'∂',altKey:true})).toBe('Control+Alt+Shift+D')
  expect(recordedVoiceShortcut({...key,code:'Space',key:' '})).toBe('Control+Shift+Space')
  expect(recordedVoiceShortcut({...key,code:'F12',key:'F12'})).toBe('Control+Shift+F12')
  for(const changed of [{isComposing:true},{keyCode:229},{repeat:true},{code:'ShiftLeft',key:'Shift'},{ctrlKey:false,shiftKey:false}]) expect(recordedVoiceShortcut({...key,...changed})).toBeNull()
})
