import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {isValidElement, type ReactElement} from 'react'
import {WidgetCard} from '@/components/chat/blocks/WidgetCard'
import {HtmlSandboxFrame} from '@/components/ui/HtmlSandboxFrame'
import {IconButton} from '@/components/ui/primitives'
import {setLocale, t} from '@/i18n'

const hooks = vi.hoisted(() => ({cursor: 0, slots: [] as any[], pending: [] as (() => void)[], cleanups: new Map<number, () => void>()}))
vi.mock('react', async original => ({...await original<typeof import('react')>(),
  useId: () => 'viewport-instance',
  useRef: (initial: unknown) => {const i = hooks.cursor++; return hooks.slots[i] ??= {current: initial}},
  useState: (initial: unknown) => {const i = hooks.cursor++; if (!(i in hooks.slots)) hooks.slots[i] = initial; return [hooks.slots[i], (value: any) => {hooks.slots[i] = typeof value === 'function' ? value(hooks.slots[i]) : value}]},
  useMemo: (factory: () => unknown, deps: unknown[]) => {const i = hooks.cursor++, old = hooks.slots[i]; if (!old || deps.some((dep, index) => dep !== old.deps[index])) hooks.slots[i] = {deps, value: factory()}; return hooks.slots[i].value},
  useEffect: (effect: () => void | (() => void), deps: unknown[]) => {const i = hooks.cursor++, old = hooks.slots[i]; if (!old || deps.some((dep, index) => dep !== old[index])) {hooks.slots[i] = deps; hooks.pending.push(() => {hooks.cleanups.get(i)?.(); hooks.cleanups.delete(i); const cleanup = effect(); if (cleanup) hooks.cleanups.set(i, cleanup)})}},
}))
vi.mock('@/hooks/useDialogFocus', () => ({useDialogFocus: () => {}}))
vi.mock('@/store/app', () => ({useAppStore: (select: (value: unknown) => unknown) => select({settingsOpen: false})}))
vi.mock('@/i18n', async original => {const module = await original<typeof import('@/i18n')>(); return {...module, useLocale: module.getLocale}})

const code = '<!doctype html><style>html,body{height:100%}.desktop{position:fixed;inset:0}</style><main class="desktop">Desktop</main>'
const payload = {title: 'Desktop 原文', kind: 'html' as const, code, chars: code.length, loadingMessages: []}
function render() {
  hooks.cursor = 0
  const child = (WidgetCard as unknown as {type: (props: unknown) => ReactElement<any>}).type({payload})
  const node = (child.type as unknown as {type: (props: unknown) => ReactElement<any>}).type(child.props)
  while (hooks.pending.length) hooks.pending.shift()!()
  return node
}
function find(node: unknown, predicate: (node: ReactElement<any>) => boolean): ReactElement<any> | undefined {
  if (Array.isArray(node)) {for (const child of node) {const hit = find(child, predicate); if (hit) return hit}; return}
  if (!isValidElement<{children?: unknown}>(node)) return
  return predicate(node) ? node : find(node.props.children, predicate)
}
const sandbox = (node: unknown) => find(node, child => child.type === HtmlSandboxFrame)!
const button = (node: unknown, key: string) => find(node, child => child.type === IconButton && child.props.label === t(key))!
beforeEach(() => {hooks.cursor = 0; hooks.slots = []; hooks.pending = []; hooks.cleanups.clear()})
afterEach(() => {hooks.cleanups.forEach(cleanup => cleanup()); setLocale('en')})

it('gives the same HTML sandbox the full remaining dialog height through zoom, source and language round trips', () => {
  let node = render()
  const original = sandbox(node)
  expect(original.props.viewKey).toBe('inline')
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    node = render()
    button(node, 'ui.widget.zoomIn').props.onClick()
    node = render()
    expect(node.props.role).toBe('dialog')
    expect(node.props.className.split(' ')).toEqual(expect.arrayContaining(['flex', 'flex-col', 'min-h-0']))
    expect(node.props.children[0].props.className).toContain('shrink-0')
    const area = node.props.children[1]
    expect(area.props.className.split(' ')).toEqual(expect.arrayContaining(['flex-1', 'min-h-0', 'overflow-auto']))
    expect(area.props.children[0].props.className).toBe('h-full')
    expect(sandbox(node).props).toMatchObject({viewKey: 'expanded', code, active: true})
    button(node, 'ui.widget.viewCode').props.onClick()
    node = render()
    expect(node.props.children[1].props.children[0].props.hidden).toBe(true)
    expect(sandbox(node).props.active).toBe(false)
    button(node, 'ui.widget.viewWidget').props.onClick()
    node = render()
    expect(sandbox(node).type).toBe(original.type)
    expect(sandbox(node).props.code).toBe(original.props.code)
    button(node, 'ui.widget.zoomOut').props.onClick()
    node = render()
    expect(node.props.role).toBeUndefined()
    expect(sandbox(node).props).toMatchObject({viewKey: 'inline', code, active: true})
    expect(node.props.children[1].props.className).not.toContain('flex-1')
  }
})
