import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import type {ReactElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {VoiceInputPage} from '../VoiceInputPage'
import {setLocale, t} from '@/i18n'
import {SETTINGS_PAGES} from '@/lib/settingsCatalog'

const hooks = vi.hoisted(() => ({cursor: 0, slots: [] as unknown[], cleanups: new Map<number, () => void>()}))
vi.mock('react', async original => ({...await original<typeof import('react')>(),
  useRef: (value: unknown) => {const index = hooks.cursor++; return hooks.slots[index] ??= {current: value}},
  useState: (value: unknown) => {const index = hooks.cursor++; if (!(index in hooks.slots)) hooks.slots[index] = value
    return [hooks.slots[index], (next: unknown) => {hooks.slots[index] = next}]},
  useEffect: (effect: () => void | (() => void), deps: unknown[]) => {
    const index = hooks.cursor++, previous = hooks.slots[index] as unknown[] | undefined
    if (!previous || deps.some((dep, i) => dep !== previous[i])) {
      hooks.cleanups.get(index)?.(); hooks.slots[index] = deps
      const cleanup = effect()
      if (cleanup) hooks.cleanups.set(index, cleanup)
    }
  },
  useSyncExternalStore: (_subscribe: unknown, snapshot: () => unknown) => snapshot(),
}))
vi.mock('@/i18n', async original => {const module = await original<typeof import('@/i18n')>(); return {...module, useLocale: module.getLocale}})

const installed = {available: true, platform: 'macos', download_url: 'https://anoma.ly/hex/download/macos-arm64-dmg', reason: null}
const fetchMock = vi.fn<typeof fetch>()
const render = () => {hooks.cursor = 0; return VoiceInputPage()}
const markup = () => renderToStaticMarkup(render())
function nodes(node: unknown): ReactElement<Record<string, any>>[] {
  if (!node || typeof node !== 'object') return []
  if (Array.isArray(node)) return node.flatMap(nodes)
  const element = node as ReactElement<Record<string, any>>
  return [element, ...nodes(element.props?.children)]
}
function open() {return nodes(render()).find(node => node.props.children === t('ui.voice.dictation.open'))!.props.onClick() as Promise<void>}
function refresh() {return nodes(render()).find(node => node.props.label === t('ui.voice.dictation.refresh'))!.props.onClick()}
function unmount() {for (const cleanup of [...hooks.cleanups.values()].reverse()) cleanup(); hooks.cleanups.clear()}
function deferred<T>() {let resolve!: (value: T) => void; const promise = new Promise<T>(done => {resolve = done}); return {promise, resolve}}

beforeEach(() => {
  hooks.cursor = 0; hooks.slots = []; hooks.cleanups.clear(); fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('document', {hidden: false, documentElement: {}, addEventListener: vi.fn(), removeEventListener: vi.fn()})
  fetchMock.mockImplementation(async () => Response.json(installed))
})
afterEach(async () => {unmount(); vi.unstubAllGlobals(); await setLocale('en')})

it('registers a searchable settings entry and only checks installation through English, Chinese, English', async () => {
  expect(SETTINGS_PAGES.find(page => page.id === 'voice')).toMatchObject({group: 'basic', labelKey: 'ui.voice.dictation.title'})
  render()
  await vi.waitFor(() => expect(markup()).not.toContain(t('ui.voice.dictation.checking')))
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const html = markup()
    expect(html).toContain(t('ui.voice.dictation.open'))
    expect(html).toContain(t('ui.voice.dictation.useMac'))
    expect(html).toContain(t('ui.voice.dictation.setup'))
    expect(html).not.toContain(t('ui.voice.dictation.requested'))
  }
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined()
})

it('shows installation and disables opening until an explicit refresh finds Hex', async () => {
  fetchMock.mockResolvedValueOnce(Response.json({...installed, available: false, reason: {key: 'ui.voice.dictation.missing'}}))
  render()
  await vi.waitFor(() => expect(markup()).toContain(t('ui.voice.dictation.install')))
  const control = nodes(render()).find(node => node.props.children === t('ui.voice.dictation.open'))!
  expect(control.props.disabled).toBe(true)
  expect(markup()).toContain(installed.download_url)
  await control.props.onClick()
  expect(fetchMock).toHaveBeenCalledTimes(1)
  refresh()
  await vi.waitFor(() => expect(nodes(render()).find(node => node.props.children === t('ui.voice.dictation.open'))!.props.disabled).toBe(false))
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it('shares one accepted request across double clicks and a closed/reopened page', async () => {
  const response = deferred<Response>()
  fetchMock.mockImplementation(async (_path, options) => options?.method === 'POST' ? response.promise : Response.json(installed))
  render()
  await vi.waitFor(() => expect(nodes(render()).find(node => node.props.children === t('ui.voice.dictation.open'))!.props.disabled).toBe(false))
  const first = open()
  await open()
  unmount(); hooks.slots = []; render()
  await vi.waitFor(() => expect(nodes(render()).find(node => node.props.children === t('ui.voice.dictation.open'))!.props.disabled).toBe(false))
  const second = open()
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
  response.resolve(Response.json({requested: true}))
  await Promise.all([first, second])
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    expect(markup()).toContain(t('ui.voice.dictation.requested'))
  }
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
})

it('does not treat a lost response as ready or automatically open again', async () => {
  fetchMock.mockImplementation(async (_path, options) => options?.method === 'POST'
    ? new Response('connection diagnostic', {status: 503}) : Response.json(installed))
  render()
  await vi.waitFor(() => expect(nodes(render()).find(node => node.props.children === t('ui.voice.dictation.open'))!.props.disabled).toBe(false))
  await open()
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    expect(markup()).toContain(t('ui.voice.dictation.unconfirmed'))
    expect(markup()).not.toContain(t('ui.voice.dictation.requested'))
  }
  expect(fetchMock.mock.calls.map(([, options]) => options?.method ?? 'GET')).toEqual(['GET', 'POST', 'GET'])
})

it.each(['hide', 'unmount'] as const)('discards a late launch receipt after %s', async action => {
  const response = deferred<Response>()
  fetchMock.mockImplementation(async (_path, options) => options?.method === 'POST' ? response.promise : Response.json(installed))
  render()
  await vi.waitFor(() => expect(nodes(render()).find(node => node.props.children === t('ui.voice.dictation.open'))!.props.disabled).toBe(false))
  const pending = open()
  if (action === 'hide') {Object.assign(document, {hidden: true}); render()} else unmount()
  response.resolve(Response.json({requested: true}))
  await pending
  expect(markup()).not.toContain(t('ui.voice.dictation.requested'))
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
})

it('cancels hidden installation reads and rejects their stale results after showing again', async () => {
  const response = deferred<Response>()
  fetchMock.mockReturnValueOnce(response.promise)
  render()
  const signal = fetchMock.mock.calls[0][1]?.signal
  Object.assign(document, {hidden: true}); render()
  expect(signal?.aborted).toBe(true)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  Object.assign(document, {hidden: false}); render()
  await vi.waitFor(() => expect(markup()).toContain(t('ui.voice.dictation.useMac')))
  response.resolve(Response.json({...installed, available: false, reason: {key: 'ui.voice.dictation.missing'}}))
  await response.promise
  await vi.waitFor(() => expect(markup()).not.toContain(t('ui.voice.dictation.missing')))
  expect(fetchMock).toHaveBeenCalledTimes(2)
})
