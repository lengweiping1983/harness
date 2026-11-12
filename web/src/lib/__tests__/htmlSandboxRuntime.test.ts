import {runInNewContext} from 'node:vm'
import {describe, expect, it, vi} from 'vitest'
import {sandboxBootstrap} from '../htmlSandboxRuntime'

function runtime() {
  const messages: Record<string, unknown>[] = []
  const events = new Map<string, (event?: any) => void>()
  const frames: (() => void)[] = []
  const observe = vi.fn(), disconnect = vi.fn()
  let resize!: () => void
  const window: Record<string, unknown> = {}
  const parent = {postMessage: (message: Record<string, unknown>) => messages.push(message)}
  const document = {body: {scrollHeight: 120, getBoundingClientRect: () => ({height: 120})}, readyState: 'loading',
    documentElement: {classList: {toggle: vi.fn()}, style: {setProperty: vi.fn(), colorScheme: ''}},
    querySelectorAll: () => [], activeElement: null}
  runInNewContext(sandboxBootstrap('document-a'), {
    parent, document, window,
    requestAnimationFrame: (callback: () => void) => frames.push(callback),
    ResizeObserver: class { constructor(callback: () => void) {resize = callback} observe = observe; disconnect = disconnect },
    addEventListener: (name: string, listener: (event?: any) => void) => events.set(name, listener),
  })
  const send = (data: Record<string, unknown> = {}, source: unknown = parent) => events.get('message')!({source, data: {
    channel: 'harness-widget', id: 'document-a', kind: 'theme', css: '--c-ink:1 2 3', active: true, paint: 'paint-a', ...data,
  }})
  const flush = () => {while (frames.length) frames.shift()!()}
  return {window, messages, events, frames, observe, disconnect, document, send, flush, resize: () => resize()}
}

describe('emitted sandbox bootstrap', () => {
  it('boots before model execution, preserves early errors, and waits for document paint', () => {
    const r = runtime()
    expect(r.messages.map(message => message.kind)).toEqual(['boot'])
    r.events.get('error')!({message: 'early failure'})
    r.messages.length = 0
    r.send()
    expect(r.messages.map(message => message.kind)).toEqual(['resize', 'error'])
    expect(r.messages.at(-1)?.message).toBe('early failure')
    r.document.readyState = 'interactive'
    r.events.get('DOMContentLoaded')!()
    r.flush()
    expect(r.messages.at(-1)).toMatchObject({id: 'document-a', kind: 'ready', paint: 'paint-a'})
  })
  it('rejects foreign windows, old documents, and unrelated messages before changing theme or layout', () => {
    const r = runtime()
    r.send({}, {})
    r.send({id: 'old-document'})
    r.send({kind: 'resize'})
    r.send({css: null})
    expect(r.observe).not.toHaveBeenCalled()
    expect(r.document.documentElement.classList.toggle).not.toHaveBeenCalled()
    expect(r.messages).toHaveLength(1)
  })
  it('coalesces resize bursts, deduplicates heights, and clamps extreme content', () => {
    const r = runtime()
    r.send()
    r.messages.length = 0
    r.document.body.scrollHeight = 640
    for (let i = 0; i < 1000; i++) r.resize()
    expect(r.frames).toHaveLength(1)
    r.flush()
    expect(r.messages).toEqual([{channel: 'harness-widget', id: 'document-a', kind: 'resize', height: 640}])
    r.resize(); r.flush()
    expect(r.messages).toHaveLength(1)
    r.document.body.scrollHeight = 1000000
    r.resize(); r.flush()
    expect(r.messages.at(-1)?.height).toBe(2000)
    r.document.body.scrollHeight++
    r.resize(); r.flush()
    expect(r.messages).toHaveLength(2)
  })
  it('disconnects hidden observation, suppresses stale paint, and resumes the same document', () => {
    const r = runtime()
    r.document.readyState = 'complete'
    r.send()
    r.messages.length = 0
    r.send({active: false, paint: null})
    expect(r.disconnect).toHaveBeenCalledTimes(2)
    r.flush()
    expect(r.messages).toHaveLength(0)
    r.resize()
    expect(r.frames).toHaveLength(0)
    r.send({paint: 'paint-b'})
    r.flush()
    expect(r.messages.at(-1)).toMatchObject({kind: 'ready', paint: 'paint-b'})
    expect(r.observe).toHaveBeenCalledTimes(2)
  })
  it('updates theme in place and relays only trusted, non-composing keyboard events', () => {
    const r = runtime()
    r.send({dark: true, trapFocus: true})
    expect(r.document.documentElement.style.setProperty).toHaveBeenCalledWith('--c-ink', '1 2 3')
    expect(r.document.documentElement.classList.toggle).toHaveBeenCalledWith('dark', true)
    const key = r.events.get('keydown')!
    r.messages.length = 0
    key({key: 'Escape', isTrusted: true, isComposing: true})
    key({key: 'Escape', isTrusted: false})
    expect(r.messages).toHaveLength(0)
    key({key: 'Escape', isTrusted: true})
    expect(r.messages.at(-1)?.kind).toBe('escape')
    const preventDefault = vi.fn()
    key({key: 'Tab', isTrusted: true, shiftKey: true, preventDefault})
    expect(preventDefault).toHaveBeenCalledOnce()
    expect(r.messages.at(-1)).toMatchObject({kind: 'focus-boundary', backward: true})
  })
})

it('waits for asynchronous visualization readiness and discards hidden paint tickets', async () => {
 const r = runtime()
 r.document.readyState = 'complete'
 let finish!: () => void
 r.window.__harnessWidgetReady = new Promise<void>(resolve => {finish = resolve})
 r.send()
 expect(r.frames).toHaveLength(0)
 r.send({active: false, paint: null})
 finish(); await Promise.resolve(); r.flush()
 expect(r.messages.some(message => message.kind === 'ready')).toBe(false)
 r.send({paint: 'current-chart'})
 await Promise.resolve(); r.flush()
 expect(r.messages.at(-1)).toMatchObject({kind: 'ready', paint: 'current-chart'})
})

it('preserves explicit owned error resource identity through the early failure handshake', () => {
 const r = runtime()
 r.events.get('unhandledrejection')!({reason:{message:'Original',harnessCopy:'ui.chart.inlineData'}})
 r.messages.length=0
 r.send()
 expect(r.messages.at(-1)).toMatchObject({kind:'error',message:'Original',copy:'ui.chart.inlineData'})
})
