import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {ConversationCheckout, ConversationCheckoutFields} from '../ConversationCheckout'
import {RefreshButton} from '@/components/ui/primitives'
import {setLocale, t} from '@/i18n'

const hooks = vi.hoisted(() => ({cursor: 0, slots: [] as any[], effects: [] as (() => void)[], cleanups: new Map<number, () => void>(), visible: true}))
vi.mock('react', async original => ({...await original<typeof import('react')>(),
  useRef: (initial: unknown) => {const index = hooks.cursor++; return hooks.slots[index] ??= {current: initial}},
  useState: (initial: unknown) => {
    const index = hooks.cursor++
    if (!(index in hooks.slots)) hooks.slots[index] = initial
    return [hooks.slots[index], (next: any) => {hooks.slots[index] = typeof next === 'function' ? next(hooks.slots[index]) : next}]
  },
  useEffect: (effect: () => void | (() => void), deps: unknown[]) => {
    const index = hooks.cursor++, previous = hooks.slots[index]
    if (!previous || deps.some((value, offset) => value !== previous[offset])) {
      hooks.slots[index] = deps
      hooks.effects.push(() => {hooks.cleanups.get(index)?.(); hooks.cleanups.delete(index); const cleanup = effect(); if (cleanup) hooks.cleanups.set(index, cleanup)})
    }
  },
}))
vi.mock('@/i18n', async original => {const module = await original<typeof import('@/i18n')>(); return {...module, useLocale: module.getLocale}})
vi.mock('@/hooks/usePageVisible', () => ({usePageVisible: () => hooks.visible}))
const nodes = (node: any): any[] => Array.isArray(node) ? node.flatMap(nodes) : node?.props ? [node, ...nodes(node.props.children)] : []
const text = (node: any): string => typeof node === 'string' ? node : Array.isArray(node) ? node.map(text).join('') : node?.props ? text(node.props.children) : ''
const options = (cwd: string) => ({cwd, is_git: false, root: null, branch: null, head: null, refs: [], local_branches: [], dirty: false})
let props: Parameters<typeof ConversationCheckout>[0], changes: ReturnType<typeof vi.fn>
function render() {
  hooks.cursor = 0
  const tree = ConversationCheckout(props)
  hooks.effects.splice(0).forEach(effect => effect())
  return tree
}
beforeEach(() => {
  hooks.cursor = 0; hooks.slots = []; hooks.effects = []; hooks.cleanups.clear(); hooks.visible = true
  changes = vi.fn((value, status) => {if (status === 'ready') props = {...props, value}})
  props = {cwd: '/project', value: null, onChange: changes}
})
afterEach(() => {hooks.cleanups.forEach(cleanup => cleanup()); vi.unstubAllGlobals(); setLocale('en')})

it('explains hidden-page pauses without reading, then automatically checks the current project on return', async () => {
  hooks.visible = false
  const fetch = vi.fn().mockResolvedValue(Response.json(options('/project')))
  vi.stubGlobal('fetch', fetch)
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    expect(text(render())).toContain(t('ui.newConversation.checkPaused'))
    expect(nodes(render()).find(node => node.type === RefreshButton)?.props.disabled).toBe(true)
  }
  expect(fetch).not.toHaveBeenCalled()
  hooks.visible = true; render()
  await vi.waitFor(() => expect(changes).toHaveBeenLastCalledWith(expect.objectContaining({cwd: '/project'}), 'ready'))
  expect(render().type).toBe(ConversationCheckoutFields)
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('offers a read-only refresh while checking and ignores the cancelled read when it arrives late', async () => {
  let release!:(response: Response) => void
  const fetch = vi.fn().mockReturnValueOnce(new Promise<Response>(done => {release = done})).mockResolvedValue(Response.json(options('/project')))
  vi.stubGlobal('fetch', fetch)
  render()
  const refresh = nodes(render()).find(node => node.type === RefreshButton)!
  expect(refresh.props.disabled).toBe(false)
  refresh.props.onClick(); render()
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true)
  await vi.waitFor(() => expect(changes).toHaveBeenLastCalledWith(expect.objectContaining({cwd: '/project'}), 'ready'))
  const obsolete = Response.json(options('/obsolete'))
  release(obsolete)
  await vi.waitFor(() => expect(obsolete.bodyUsed).toBe(true))
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(props.value?.cwd).toBe('/project')
  expect(changes.mock.calls.filter(([, status]) => status === 'ready')).toHaveLength(1)
})

it('cancels on hide and checks a replacement directory only after the page returns', async () => {
  let release!:(response: Response) => void
  const fetch = vi.fn().mockReturnValueOnce(new Promise<Response>(done => {release = done})).mockResolvedValue(Response.json(options('/next')))
  vi.stubGlobal('fetch', fetch)
  render(); hooks.visible = false; render()
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true)
  props = {...props, cwd: '/next'}; render()
  const obsolete = Response.json(options('/project'))
  release(obsolete)
  await vi.waitFor(() => expect(obsolete.bodyUsed).toBe(true))
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(changes.mock.calls.some(([, status]) => status === 'ready')).toBe(false)
  hooks.visible = true; render()
  await vi.waitFor(() => expect(changes).toHaveBeenLastCalledWith(expect.objectContaining({cwd: '/next'}), 'ready'))
  expect(fetch.mock.calls[1][0]).toContain('path=%2Fnext')
})
