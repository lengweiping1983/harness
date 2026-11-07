import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {TerminalTypeSelect} from '../TerminalTypeSelect'
import {setLocale, t} from '@/i18n'

const hooks = vi.hoisted(() => ({cursor: 0, slots: [] as unknown[], cleanups: new Map<number, () => void>()}))
vi.mock('react', async original => {
  const module = await original<typeof import('react')>()
  return {...module,
    useState: (initial: unknown) => {
      const index = hooks.cursor++
      if (!(index in hooks.slots)) hooks.slots[index] = typeof initial === 'function' ? initial() : initial
      return [hooks.slots[index], (next: unknown) => {hooks.slots[index] = typeof next === 'function' ? next(hooks.slots[index]) : next}]
    },
    useEffect: (run: () => void | (() => void), dependencies: unknown[]) => {
      const index = hooks.cursor++
      const previous = hooks.slots[index] as unknown[] | undefined
      if (previous && dependencies.every((value, i) => value === previous[i])) return
      hooks.cleanups.get(index)?.()
      hooks.cleanups.delete(index)
      hooks.slots[index] = dependencies
      const cleanup = run()
      if (cleanup) hooks.cleanups.set(index, cleanup)
    },
    useSyncExternalStore: (_subscribe: unknown, snapshot: () => unknown) => snapshot(),
  }
})
vi.mock('@/i18n', async original => {
  const module = await original<typeof import('@/i18n')>()
  return {...module, useLocale: module.getLocale}
})

function render(active: boolean) {
  hooks.cursor = 0
  return renderToStaticMarkup(TerminalTypeSelect({active}))
}
beforeEach(() => {hooks.slots = []; hooks.cleanups.clear()})
afterEach(async () => {
  for (const cleanup of hooks.cleanups.values()) cleanup()
  vi.unstubAllGlobals()
  await setLocale('en')
})

it('shows no phantom loading status while inactive and rejects results from before hiding', async () => {
  const pending: {resolve: (value: Response) => void; signal: AbortSignal}[] = []
  const fetch = vi.fn((_url, options) => new Promise<Response>(resolve => pending.push({resolve, signal: options.signal})))
  vi.stubGlobal('fetch', fetch)
  expect(render(false)).toBe('')
  expect(fetch).not.toHaveBeenCalled()
  expect(render(true)).toContain(t('ui.terminalTypes.loading'))
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(render(false)).toBe('')
  expect(pending[0].signal.aborted).toBe(true)
  render(true)
  pending[1].resolve(new Response(JSON.stringify({enabled: true, backends: ['fresh'], default_type: 'fresh', managed_runtime: false})))
  await vi.waitFor(() => expect(render(true)).toContain('fresh'))
  pending[0].resolve(new Response(JSON.stringify({enabled: true, backends: ['stale'], default_type: 'stale', managed_runtime: false})))
  await Promise.resolve()
  expect(render(true)).not.toContain('stale')
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('renders a resource failure in English Chinese English without repeating the request', async () => {
  const fetch = vi.fn().mockRejectedValue(new TypeError('Original network diagnostic'))
  vi.stubGlobal('fetch', fetch)
  render(true)
  await vi.waitFor(() => expect(render(true)).toContain('role="alert"'))
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const markup = render(true)
    expect(markup).toContain(t('ui.http.networkUnavailable'))
    expect(markup).not.toContain('[object Object]')
  }
  expect(fetch).toHaveBeenCalledTimes(1)
})
