import type {ResourceCopy} from '@/types'
import {isMac} from '@/lib/utils'

export const DEFAULT_VOICE_SHORTCUT = 'Control+Alt+Space'

/** 记录实际组合键，组合输入、自动重复与单独修饰键不修改已存快捷键。 */
export function recordedVoiceShortcut(event: Pick<KeyboardEvent, 'code' | 'key' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey' | 'repeat' | 'isComposing' | 'keyCode'>): string | null {
  if (event.isComposing || event.keyCode === 229 || event.repeat) return null
  let key = /^(Key[A-Z]|Digit[0-9])$/.test(event.code) ? event.code.replace(/^(Key|Digit)/, '') : ''
  if (/^F([1-9]|1\d|2[0-4])$/.test(event.key)) key = event.key
  const named: Record<string, string> = {Space:'Space', Enter:'Enter', Tab:'Tab', Backspace:'Backspace', Delete:'Delete', Insert:'Insert', Home:'Home', End:'End', PageUp:'PageUp', PageDown:'PageDown', ArrowUp:'Up', ArrowDown:'Down', ArrowLeft:'Left', ArrowRight:'Right'}
  if (Object.hasOwn(named, event.code)) key = named[event.code]
  if (!key || !(event.ctrlKey || event.altKey || event.metaKey || event.shiftKey)) return null
  return [event.ctrlKey && 'Control', event.altKey && 'Alt', event.metaKey && 'Command', event.shiftKey && 'Shift', key].filter(Boolean).join('+')
}

/** 显示名称与 Electron 加速键分开，保存时只转换明确的按键别名。 */
export function voiceShortcutAccelerator(value: string): string {
  const aliases: Record<string, string> = {Ctrl:'Control', '⌃':'Control', Option:'Alt', '⌥':'Alt', Cmd:'Command', '⌘':'Command', '⇧':'Shift'}
  return value.split('+').map(key => {key = key.trim(); return Object.hasOwn(aliases, key) ? aliases[key] : key}).join('+')
}

export function formatVoiceShortcut(value: string, mac = isMac()): string {
  const names: Record<string, string> = mac
    ? {Control:'⌃', Alt:'⌥', Command:'⌘', CommandOrControl:'⌘', Shift:'⇧'}
    : {Control:'Ctrl', CommandOrControl:'Ctrl'}
  return voiceShortcutAccelerator(value).split('+').map(key => Object.hasOwn(names, key) ? names[key] : key).join(' + ')
}

export interface DesktopVoiceStatus {
  supported: boolean
  enabled: boolean
  shortcut: string
  accessibility: boolean
  phase: string
  error: ResourceCopy | null
}
export type VoiceReply<T> = {ok: true; data: T} | {ok: false; copy: ResourceCopy; diagnostic?: string}
export interface VoiceAction {action: 'start' | 'stop' | 'cancel' | 'error'; id?: string; copy?: ResourceCopy; diagnostic?: string}
export interface DesktopVoiceBridge {
  context(value: {id: string; sessionId: string | null; revision: number; generation: number; composing: boolean; editor: boolean}): Promise<VoiceReply<null>>
  receipt(id: string, accepted: boolean): Promise<VoiceReply<null>>
  onInsert(listener: (value: {id: string; context: {id: string; sessionId: string | null; revision: number; generation: number; composing: boolean; editor: boolean}; transcript: string}) => void): () => void
  status(): Promise<VoiceReply<DesktopVoiceStatus>>
  configure(value: {enabled: boolean; shortcut: string}): Promise<VoiceReply<DesktopVoiceStatus>>
  permission(): Promise<VoiceReply<DesktopVoiceStatus>>
  ready(): Promise<VoiceReply<VoiceAction | null>>
  phase(id: string, phase: 'recording' | 'processing' | 'done'): Promise<VoiceReply<null>>
  complete(id: string, transcript: string): Promise<VoiceReply<{result: 'draft' | 'sent' | 'copied'}>>
  cancel(id?: string): Promise<VoiceReply<null>>
  onAction(listener: (action: VoiceAction) => void): () => void
}
export function desktopVoice(): DesktopVoiceBridge | undefined {
  if (typeof window === 'undefined') return undefined
  return (window as unknown as {harnessDesktop?: {voice?: DesktopVoiceBridge}}).harnessDesktop?.voice
}
