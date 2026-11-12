import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import type {ReactElement} from 'react'
import {VoiceButton} from '../VoiceButton'
import {useAppStore} from '@/store/app'
import {setLocale, t} from '@/i18n'
import {ResourceError} from '@/lib/resourceError'

const hooks = vi.hoisted(() => ({cursor: 0, slots: [] as unknown[], cleanups: [] as (() => void)[], events: new Map<string, (event: unknown) => void>()}))
const recording = vi.hoisted(() => ({create: vi.fn(), request: vi.fn(), prepare: vi.fn()}))
vi.mock('react', async original => ({...await original<typeof import('react')>(),
  useRef: (value: unknown) => {const index = hooks.cursor++; return hooks.slots[index] ??= {current: value}},
  useState: (value: unknown) => {const index = hooks.cursor++; if (!(index in hooks.slots)) hooks.slots[index] = value
    return [hooks.slots[index], (next: unknown) => {hooks.slots[index] = next}]},
  useEffect: (effect: () => void | (() => void), deps: unknown[]) => {
    const index = hooks.cursor++, previous = hooks.slots[index] as unknown[] | undefined
    if (!previous || deps.some((dep, i) => dep !== previous[i])) {
      hooks.slots[index] = deps; const cleanup = effect(); if (cleanup) hooks.cleanups.push(cleanup)
    }
  },
  useSyncExternalStore: (_subscribe: unknown, snapshot: () => unknown) => snapshot(),
}))
vi.mock('@/i18n', async original => {const module = await original<typeof import('@/i18n')>(); return {...module, useLocale: module.getLocale}})
vi.mock('react-dom', () => ({createPortal: (node: unknown) => node}))
vi.mock('@/lib/voiceRecorder', () => ({createVoiceRecorder: recording.create}))
vi.mock('@/lib/http', async original => ({...await original<typeof import('@/lib/http')>(), request:(...args:unknown[]) => (args[0]==='/api/v1/voice/preparations' ? recording.prepare : recording.request)(...args)}))
const initialApp = useAppStore.getState()
const editor = {current: {getBoundingClientRect: () => ({top: 400, right: 600}), closest: () => ({contains: () => true})} as unknown as HTMLTextAreaElement}
const onTranscript = vi.fn()
function render(disabled = false) {
  hooks.cursor = 0
  return VoiceButton({editor, disabled, onTranscript})
}
function button(node: unknown): ReactElement<Record<string, any>> {
  const element = node as ReactElement<Record<string, any>>
  if (element.props?.onClick) return element
  for (const child of [element.props?.children].flat()) {
    if (child && typeof child === 'object') {const result = button(child); if (result) return result}
  }
  return undefined as never
}
beforeEach(() => {
  hooks.slots = []; hooks.cleanups = []; hooks.events.clear()
  onTranscript.mockClear(); recording.create.mockReset(); recording.request.mockReset();recording.prepare.mockReset().mockResolvedValue({ok:true,data:{ready:true}})
  vi.stubGlobal('window', {innerWidth: 800, innerHeight: 800})
  vi.stubGlobal('document', {hidden: false, body: {}, documentElement: {}, activeElement: editor.current,
    addEventListener: (name: string, handler: (event: unknown) => void) => hooks.events.set(name, handler),
    removeEventListener: (name: string) => hooks.events.delete(name)})
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: vi.fn()}})
  vi.stubGlobal('AudioContext', class {})
  vi.stubGlobal('AudioWorkletNode', class {})
  useAppStore.setState({activeSessionId: 'voice-a', newSessionRevision: 1})
})
afterEach(async () => {
  hooks.cleanups.reverse().forEach(cleanup => cleanup())
  useAppStore.setState(initialApp); vi.unstubAllGlobals(); await setLocale('en')
})

it('keeps the actual microphone localized through English, Chinese and English', async () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    expect(button(render()).props['aria-label']).toBe(t('ui.voice.start'))
  }
  expect(recording.create).not.toHaveBeenCalled()
})

it('renders native permission guidance in the current language and retries from the same microphone', async () => {
  const dispose = vi.fn()
  recording.create.mockImplementation(() => ({ready:Promise.reject(new ResourceError({key:'ui.systemPermission.microphone.settings'})), dispose}))
  const text = (node:unknown):string => typeof node === 'string' ? node : Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object' ? text((node as ReactElement<{children?:unknown}>).props?.children) : ''
  await button(render()).props.onClick()
  for (const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale)
    expect(text(render())).toContain(t('ui.systemPermission.microphone.settings'))
  }
  expect(recording.create).toHaveBeenCalledOnce()
  await button(render()).props.onClick()
  expect(recording.create).toHaveBeenCalledTimes(2)
  expect(recording.request).not.toHaveBeenCalled()
  expect(onTranscript).not.toHaveBeenCalled()
})

it('shows a focused preparation prompt while microphone permission is pending', async () => {
  let ready!: (value: {finish: () => Promise<ArrayBuffer>; dispose: () => void}) => void
  recording.create.mockReturnValue({ready: new Promise(resolve => {ready = resolve}), dispose: vi.fn()})
  void button(render()).props.onClick()
  await vi.waitFor(() => expect(recording.create).toHaveBeenCalledOnce())
  const pending = render()
  expect(button(pending).props['aria-label']).toBe(t('ui.voice.opening'))
  const content = (node: unknown): string => {
    if (typeof node === 'string') return node
    if (Array.isArray(node)) return node.map(content).join('')
    return node && typeof node === 'object' ? content((node as ReactElement<{children?: unknown}>).props?.children) : ''
  }
  expect(content(pending)).toContain(t('ui.voice.opening'))
  expect(recording.prepare).not.toHaveBeenCalled()
  ready({finish: async () => new ArrayBuffer(48), dispose: vi.fn()})
  await vi.waitFor(() => expect(button(render()).props['aria-label']).toBe(t('ui.voice.stop')))
  expect(recording.prepare).toHaveBeenCalledExactlyOnceWith('/api/v1/voice/preparations', {method:'POST', timeout:630_000})
})

it('prepares during recording and waits for that single result before posting audio', async () => {
  const finish=vi.fn(async () => new ArrayBuffer(48)),dispose=vi.fn()
  let prepared!:(value:unknown) => void
  recording.create.mockReturnValue({ready:Promise.resolve({finish,dispose}),dispose})
  recording.prepare.mockReturnValue(new Promise(resolve => {prepared=resolve}))
  recording.request.mockResolvedValue({ok:true,data:{transcript:'prepared speech'}})
  await button(render()).props.onClick()
  expect(recording.prepare).toHaveBeenCalledOnce()
  expect(button(render()).props['aria-label']).toBe(t('ui.voice.stop'))
  const stopping=button(render()).props.onClick()
  await vi.waitFor(() => expect(finish).toHaveBeenCalledOnce())
  expect(recording.request).not.toHaveBeenCalled()
  prepared({ok:true,data:{ready:true}})
  await stopping
  expect(recording.request).toHaveBeenCalledOnce()
  expect(onTranscript).toHaveBeenCalledExactlyOnceWith('prepared speech')
})

it.each(['failure','switch','unmount'])('does not replay preparation or post audio after preparation %s', async kind => {
  const dispose=vi.fn()
  recording.create.mockReturnValue({ready:Promise.resolve({finish:async () => new ArrayBuffer(48),dispose}),dispose})
  let prepared!:(value:unknown) => void
  recording.prepare.mockReturnValue(new Promise(resolve => {prepared=resolve}))
  await button(render()).props.onClick()
  const stopping=button(render()).props.onClick()
  if(kind==='switch')useAppStore.setState({activeSessionId:'another'})
  if(kind==='unmount')hooks.cleanups.splice(0).reverse().forEach(cleanup => cleanup())
  prepared({ok:false,status:503,code:'voice/unavailable',message:'failed',details:{copy:{key:'ui.voice.engineFailed'}}})
  await stopping
  expect(recording.prepare).toHaveBeenCalledOnce()
  expect(recording.request).not.toHaveBeenCalled()
  expect(onTranscript).not.toHaveBeenCalled()
})

it('serializes double clicks, appends the transcript once and never sends a message', async () => {
  const finish = vi.fn(async () => new ArrayBuffer(48)), dispose = vi.fn()
  recording.create.mockReturnValue({ready: Promise.resolve({finish, dispose}), dispose})
  let respond!: (result: unknown) => void
  recording.request.mockReturnValue(new Promise(resolve => {respond = resolve}))
  await button(render()).props.onClick()
  const stopping = button(render()).props.onClick()
  await vi.waitFor(() => expect(recording.request).toHaveBeenCalledOnce())
  await button(render()).props.onClick()
  expect(finish).toHaveBeenCalledOnce()
  respond({ok: true, data: {transcript: '原文 speech'}})
  await stopping
  expect(onTranscript).toHaveBeenCalledExactlyOnceWith('原文 speech')
  expect(recording.request.mock.calls[0][0]).toBe('/api/v1/voice/transcriptions')
})

it.each(['switch', 'round-trip', 'hidden', 'unmount'] as const)('discards a late transcript after %s', async kind => {
  const dispose = vi.fn()
  recording.create.mockReturnValue({ready: Promise.resolve({finish: async () => new ArrayBuffer(48), dispose}), dispose})
  let respond!: (result: unknown) => void
  recording.request.mockReturnValue(new Promise(resolve => {respond = resolve}))
  await button(render()).props.onClick()
  const stopping = button(render()).props.onClick()
  await vi.waitFor(() => expect(recording.request).toHaveBeenCalledOnce())
  if (kind === 'switch' || kind === 'round-trip') useAppStore.setState({activeSessionId: 'voice-b'})
  if (kind === 'round-trip') useAppStore.setState({activeSessionId: 'voice-a'})
  if (kind === 'hidden') {Object.assign(document, {hidden: true}); hooks.events.get('visibilitychange')!({})}
  if (kind === 'unmount') hooks.cleanups.splice(0).reverse().forEach(cleanup => cleanup())
  respond({ok: true, data: {transcript: 'stale speech'}})
  await stopping
  expect(onTranscript).not.toHaveBeenCalled()
  expect(dispose).toHaveBeenCalled()
})

it('ignores composition and repeated shortcut events before accepting one shortcut', async () => {
  const dispose = vi.fn()
  recording.create.mockReturnValue({ready: Promise.resolve({finish: vi.fn(), dispose}), dispose})
  render()
  const handler = hooks.events.get('keydown')!
  const event = {ctrlKey: true, shiftKey: true, code: 'Space', preventDefault: vi.fn()}
  handler({...event, isComposing: true}); handler({...event, keyCode: 229}); handler({...event, repeat: true})
  expect(recording.create).not.toHaveBeenCalled()
  handler(event)
  await vi.waitFor(() => expect(recording.create).toHaveBeenCalledOnce())
  expect(event.preventDefault).toHaveBeenCalledOnce()
})


it('keeps a specific engine failure bilingual and exposes its original diagnostic without replay', async () => {
  const dispose = vi.fn()
  recording.create.mockReturnValue({ready: Promise.resolve({finish: async () => new ArrayBuffer(48), dispose}), dispose})
  recording.request.mockResolvedValue({ok: false, status: 503, code: 'voice/unavailable', message: 'original error',
    details: {copy: {key: 'ui.voice.binaryMissing', params: {path: '/fixture/hex'}}, reason: 'raw diagnostic'}})
  await button(render()).props.onClick()
  await button(render()).props.onClick()
  function text(node: unknown): string {
    if (typeof node === 'string') return node
    if (Array.isArray(node)) return node.map(text).join('')
    return node && typeof node === 'object' ? text((node as ReactElement<{children?: unknown}>).props?.children) : ''
  }
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const content = text(render())
    expect(content).toContain(t('ui.voice.binaryMissing', {path: '/fixture/hex'}))
    expect(content).toContain(t('ui.voice.diagnostic'))
    expect(content).toContain('raw diagnostic')
  }
  expect(recording.request).toHaveBeenCalledOnce()
  expect(onTranscript).not.toHaveBeenCalled()
})

it('draws real microphone levels only into the recording operation that owns the waveform', async () => {
  const dispose = vi.fn()
  recording.create.mockReturnValue({ready: Promise.resolve({finish: vi.fn(), dispose}), dispose})
  await button(render()).props.onClick()
  const control = button(render())
  const waveform = control.props.children as ReactElement<{elementRef: {current: SVGSVGElement | null}}>
  const lines = Array.from({length: 5}, () => ({setAttribute: vi.fn()}))
  waveform.props.elementRef.current = {querySelectorAll: () => lines} as unknown as SVGSVGElement
  const level = recording.create.mock.calls[0][1] as (value: number) => void
  level(.1)
  const quiet = Number(lines[2].setAttribute.mock.calls.at(-1)![1])
  level(.8)
  expect(Number(lines[2].setAttribute.mock.calls.at(-1)![1])).toBeGreaterThan(quiet)
  const count = lines[2].setAttribute.mock.calls.length
  useAppStore.setState({activeSessionId: 'voice-b'})
  useAppStore.setState({activeSessionId: 'voice-a'})
  level(1)
  expect(lines[2].setAttribute).toHaveBeenCalledTimes(count)
  expect(recording.request).not.toHaveBeenCalled()
})


it.each(['MacIntel', 'Win32'])('preserves the microphone and the same record/stop operation on %s', async platform => {
  Object.assign(navigator, {platform})
  const dispose = vi.fn()
  recording.create.mockReturnValue({ready: Promise.resolve({finish: async () => new ArrayBuffer(48), dispose}), dispose})
  recording.request.mockResolvedValue({ok: true, data: {transcript: 'same speech'}})
  const idle = button(render())
  expect((idle.props.children as ReactElement<{className: string}>).type).toHaveProperty('displayName', 'Mic')
  await idle.props.onClick()
  expect(button(render()).props['aria-label']).toBe(t('ui.voice.stop'))
  await button(render()).props.onClick()
  expect(onTranscript).toHaveBeenCalledExactlyOnceWith('same speech')
})

it('global dictation appends only to its original focused draft and never replays a delivered insertion', async () => {
  let insert!: (value: any) => void
  const context = vi.fn(async (_value: unknown) => ({ok:true,data:null})), receipt = vi.fn(async () => ({ok:true,data:null}))
  Object.assign(window, {harnessDesktop:{voice:{context,receipt,onInsert:(listener:(value:any) => void) => {insert=listener; return vi.fn()}}}})
  vi.stubGlobal('crypto', {randomUUID:() => 'voice-editor-identity'})
  render()
  const original=context.mock.calls.at(-1)![0]
  const value={id:'owned-dictation',context:original,transcript:'original speech'}
  insert(value); insert(value)
  expect(onTranscript).toHaveBeenCalledExactlyOnceWith('original speech')
  expect(receipt).toHaveBeenCalledTimes(2)
  expect(receipt).toHaveBeenLastCalledWith('owned-dictation',true)
  expect(recording.create).not.toHaveBeenCalled()
  expect(recording.request).not.toHaveBeenCalled()
})

it.each(['switch','round-trip','composition','disabled','unmount'] as const)('global dictation rejects a stale draft after %s', async kind => {
  let insert!: (value:any) => void
  const context=vi.fn(async (_value:unknown) => ({ok:true,data:null})), receipt=vi.fn(async () => ({ok:true,data:null}))
  Object.assign(window,{harnessDesktop:{voice:{context,receipt,onInsert:(listener:(value:any) => void) => {insert=listener; return vi.fn()}}}})
  vi.stubGlobal('crypto',{randomUUID:() => 'voice-editor-identity'})
  render()
  const original=context.mock.calls.at(-1)![0]
  if (kind==='switch' || kind==='round-trip') useAppStore.setState({activeSessionId:'voice-b'})
  if (kind==='round-trip') useAppStore.setState({activeSessionId:'voice-a'})
  if (kind==='composition') hooks.events.get('compositionstart')!({})
  if (kind==='disabled') render(true)
  if (kind==='unmount') hooks.cleanups.splice(0).reverse().forEach(cleanup => cleanup())
  insert({id:'late-dictation',context:original,transcript:'stale speech'})
  expect(onTranscript).not.toHaveBeenCalled()
  if (kind==='unmount') expect(receipt).not.toHaveBeenCalled()
  else expect(receipt).toHaveBeenLastCalledWith('late-dictation',false)
})

it('processing feedback stays generic on every recording through English, Chinese, English', async () => {
  const dispose=vi.fn()
  recording.create.mockReturnValue({ready:Promise.resolve({finish:async () => new ArrayBuffer(48),dispose}),dispose})
  let respond!: (result:unknown) => void
  recording.request.mockReturnValue(new Promise(resolve => {respond=resolve}))
  await button(render()).props.onClick()
  const stopping=button(render()).props.onClick()
  await vi.waitFor(() => expect(recording.request).toHaveBeenCalledOnce())
  for (const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale)
    expect(button(render()).props['aria-label']).toBe(t('ui.voice.processing'))
    expect(t('ui.voice.processing')).not.toMatch(/first|首次|download/i)
  }
  respond({ok:true,data:{transcript:'speech'}})
  await stopping
})
