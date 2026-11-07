import {afterEach, expect, it, vi} from 'vitest'
import copy from '@/i18n/startup.generated.json'

type Element = {
  className: string; textContent: string; children: Element[]; attributes: Record<string, string>
  append: (...children: Element[]) => void; setAttribute: (name: string, value: string) => void
}
function element(): Element {
  const node: Element = {className: '', textContent: '', children: [], attributes: {},
    append: (...children) => {node.children.push(...children)},
    setAttribute: (name, value) => {node.attributes[name] = value}}
  return node
}

let completeVoice: (() => void) | undefined
afterEach(async () => {
  completeVoice?.()
  await vi.dynamicImportSettled()
  vi.doUnmock('@/voice')
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

it.each(['en', 'zh-CN', 'en'] as const)('sets voice startup lang to %s before the deferred module resolves and keeps failure text aligned', async language => {
  vi.resetModules()
  vi.useFakeTimers()
  completeVoice = undefined
  vi.doMock('@/voice', () => new Promise<Record<string, unknown>>(resolve => {completeVoice = () => resolve({})}))
  const root = {replaceChildren: vi.fn()}
  const html = {lang: language === 'en' ? 'zh-CN' : 'en'}
  vi.stubGlobal('document', {documentElement: html, getElementById: () => root, createElement: element})
  vi.stubGlobal('window', {location: {pathname: '/voice.html', reload: vi.fn()}})
  vi.stubGlobal('localStorage', {getItem: (key: string) => key === 'harness.language.effective.v1' ? language : null})
  await import('@/voice-bootstrap')
  expect(html.lang).toBe(language)
  let frame = root.replaceChildren.mock.lastCall![0] as Element
  expect(frame.children[0].textContent).toBe(copy[language]['ui.App.f76a8d866552'])
  expect(frame.children[0].attributes.role).toBe('status')
  await vi.waitFor(() => expect(completeVoice).toBeTypeOf('function'))
  await vi.advanceTimersByTimeAsync(10000)
  frame = root.replaceChildren.mock.lastCall![0] as Element
  expect(frame.children[0].textContent).toBe(copy[language]['ui.startup.failed'])
  expect(frame.children[0].attributes.role).toBe('alert')
  expect(frame.children[1].textContent).toBe(copy[language]['ui.deferred.reload'])
  expect(html.lang).toBe(language)
  completeVoice!()
  await vi.dynamicImportSettled()
  expect(vi.getTimerCount()).toBe(0)
})
