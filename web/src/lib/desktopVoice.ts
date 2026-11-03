import type {ResourceCopy} from '@/types'

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
