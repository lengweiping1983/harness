import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import type {ReactElement} from 'react'
import {HtmlSandboxFrame} from '@/components/ui/HtmlSandboxFrame'
import {setLocale, t} from '@/i18n'

const hooks = vi.hoisted(() => ({cursor: 0, slots: [] as any[], pending: [] as (() => void)[], cleanups: new Map<number, () => void>()}))
vi.mock('react', async original => ({...await original<typeof import('react')>(),
  useRef: (initial: unknown) => {const i = hooks.cursor++; return hooks.slots[i] ??= {current: initial}},
  useState: (initial: unknown) => {const i = hooks.cursor++; if (!(i in hooks.slots)) hooks.slots[i] = initial; return [hooks.slots[i], (value: any) => {hooks.slots[i] = typeof value === 'function' ? value(hooks.slots[i]) : value}]},
  useMemo: (factory: () => unknown, deps: unknown[]) => {const i = hooks.cursor++, old = hooks.slots[i]; if (!old || deps.some((dep, index) => dep !== old.deps[index])) hooks.slots[i] = {deps, value: factory()}; return hooks.slots[i].value},
  useEffect: (effect: () => void | (() => void), deps: unknown[]) => {const i = hooks.cursor++, old = hooks.slots[i]; if (!old || deps.some((dep, index) => dep !== old[index])) {hooks.slots[i] = deps; hooks.pending.push(() => {hooks.cleanups.get(i)?.(); hooks.cleanups.delete(i); const cleanup = effect(); if (cleanup) hooks.cleanups.set(i, cleanup)})}},
}))
vi.mock('@/i18n', async original => {const module = await original<typeof import('@/i18n')>(); return {...module, useLocale: module.getLocale}})
const visibility = vi.hoisted(() => ({visible: true}))
vi.mock('@/hooks/usePageVisible', () => ({usePageVisible: () => visibility.visible}))
let listener: ((event: unknown) => void) | undefined
let parses = 0
const child = {postMessage: vi.fn()}
const root = {classList: {contains: () => false}, lang: ''}
function render(code = '<button>original</button>', active = true) {
  hooks.cursor = 0
  const node = (HtmlSandboxFrame as unknown as {type: (props: unknown) => ReactElement<any>}).type({code, active, title: 'Preview'})
  const iframe = node.props.children.find((child: any) => child?.type === 'iframe')
  iframe.ref.current = {contentWindow: child}
  while (hooks.pending.length) hooks.pending.shift()!()
  return {node, iframe}
}
const latestTheme = () => child.postMessage.mock.calls.at(-1)![0]
const message = (data: unknown, source: unknown = child) => listener?.({source, data})
beforeEach(() => {
  vi.useFakeTimers(); hooks.slots = []; hooks.cleanups.clear(); hooks.pending = []; parses = 0; visibility.visible = true; child.postMessage.mockClear()
  vi.stubGlobal('document', {documentElement: root})
  vi.stubGlobal('getComputedStyle', () => ({getPropertyValue: () => '1 2 3'}))
  vi.stubGlobal('MutationObserver', class {observe() {} disconnect() {}})
  vi.stubGlobal('window', {addEventListener: (_: string, handler: any) => {listener = handler}, removeEventListener: () => {listener = undefined}})
  vi.stubGlobal('DOMParser', class {parseFromString() {parses++; return {querySelectorAll: () => [], documentElement: {...root, classList: {toggle() {}}, outerHTML: '<html></html>'}, createElement: () => ({setAttribute() {}}), head: {prepend() {}}}}})
})
afterEach(() => {hooks.cleanups.forEach(cleanup => cleanup()); vi.useRealTimers(); vi.unstubAllGlobals(); setLocale('en')})
it('bounds initialization, localizes the preserved failure, and accepts a late ready only for the current document and paint', () => {
  render()
  const theme = latestTheme()
  vi.advanceTimersByTime(10000)
  let result = render()
  expect(result.node.props['aria-busy']).toBe(false)
  const alert = () => result.node.props.children.find((child: any) => child?.props?.role === 'alert')
  expect(alert()).toBeTruthy()
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language); result = render()
    expect(alert().props.children[0].props.children[0].props.children).toBe(t('ui.widget.initializationFailed'))
  }
  message({...theme, kind: 'ready'}, {})
  message({...theme, kind: 'ready', paint: 'old-paint'})
  expect(render().node.props.children.some((child: any) => child?.props?.role === 'alert')).toBe(true)
  message({...theme, kind: 'ready'})
  result = render()
  expect(result.iframe.props.tabIndex).toBe(0)
  expect(alert()).toBeUndefined()
  expect(parses).toBe(1)
})
it('keeps source stable across theme, visibility and source switches, rejects old messages after code replacement', () => {
  const first = render(), old = latestTheme()
  message({...old, kind: 'ready'})
  message({...old, kind: 'resize', height: 640})
  expect(render().iframe.props.style.height).toBe(640)
  render(undefined, false)
  expect(latestTheme().active).toBe(false)
  vi.advanceTimersByTime(20000)
  const resumed = render()
  expect(resumed.iframe.props.srcDoc).toBe(first.iframe.props.srcDoc)
  expect(parses).toBe(1)
  message({...latestTheme(), kind: 'ready'})
  expect(render().iframe.props.tabIndex).toBe(0)
  const second = render('<button>replacement</button>')
  expect(second.iframe.key).not.toBe(first.iframe.key)
  message({...old, kind: 'error', message: 'stale failure'})
  message({...old, kind: 'resize', height: 1900})
  const current = render('<button>replacement</button>')
  expect(current.iframe.props.style.height).toBe(40)
  expect(current.node.props.children.some((child: any) => child?.props?.role === 'alert')).toBe(false)
  expect(parses).toBe(2)
})

it('localizes explicit chart failures in place and refuses unregistered resource identities', () => {
 render()
 const theme=latestTheme()
 message({...theme,kind:'error',message:'Original',copy:'ui.chart.inlineData'})
 for(const locale of ['en','zh-CN','en'] as const){
  setLocale(locale)
  const {node}=render()
  const alert=node.props.children.find((child:any)=>child?.props?.role==='alert')
  expect(alert.props.children[0].props.children[1].props.children).toBe(t('ui.chart.inlineData'))
 }
 message({...theme,kind:'error',message:'External',copy:'constructor'})
 const alert=render().node.props.children.find((child:any)=>child?.props?.role==='alert')
 expect(alert.props.children[0].props.children[1].props.children).toBe('External')
 expect(parses).toBe(1)
})
