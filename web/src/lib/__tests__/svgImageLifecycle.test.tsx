import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {isValidElement, type ReactElement} from 'react'
import {LiveChart} from '@/components/markdown/LiveChart'
import {SvgImage} from '@/components/ui/SvgImage'
import {IconButton} from '@/components/ui/primitives'
import {setLocale, t} from '@/i18n'

// 真实组件与解析器，钩子宿主负责同一挂载实例的状态、effect 和收尾。
const hooks = vi.hoisted(() => ({cursor: 0, slots: [] as any[], pending: [] as (() => void)[], cleanups: new Map<number, () => void>()}))
vi.mock('react', async original => ({...await original<typeof import('react')>(),
  useId: () => 'svg-instance',
  useRef: (initial: unknown) => {const i = hooks.cursor++; return hooks.slots[i] ??= {current: initial}},
  useState: (initial: unknown) => {const i = hooks.cursor++; if (!(i in hooks.slots)) hooks.slots[i] = typeof initial === 'function' ? initial() : initial; return [hooks.slots[i], (value: any) => {hooks.slots[i] = typeof value === 'function' ? value(hooks.slots[i]) : value}]},
  useEffect: (effect: () => void | (() => void), deps: unknown[]) => {const i = hooks.cursor++, old = hooks.slots[i]; if (!old || deps.some((dep, index) => dep !== old[index])) {hooks.slots[i] = deps; hooks.pending.push(() => {hooks.cleanups.get(i)?.(); hooks.cleanups.delete(i); const cleanup = effect(); if (cleanup) hooks.cleanups.set(i, cleanup)})}},
}))
const state = vi.hoisted(() => ({settingsOpen: false, visible: true}))
vi.mock('@/store/app', () => ({useAppStore: (select: (value: unknown) => unknown) => select(state)}))
vi.mock('@/hooks/usePageVisible', () => ({usePageVisible: () => state.visible}))
vi.mock('@/hooks/useDialogFocus', () => ({useDialogFocus: () => {}}))
vi.mock('@/i18n', async original => {const module = await original<typeof import('@/i18n')>(); return {...module, useLocale: module.getLocale}})

const source = (version: number) => `<svg viewBox="0 0 100 50"><rect width="${version}"><animate attributeName="x" values="0;50;0" dur="2s" repeatCount="indefinite"/></rect></svg>`
function render(code: string, closed = true) {
  hooks.cursor = 0
  const child = (LiveChart as unknown as {type: (props: unknown) => ReactElement<any>}).type({code, lang: 'svg', closed})
  const node = (child.type as (props: unknown) => ReactElement<any>)(child.props)
  while (hooks.pending.length) hooks.pending.shift()!()
  return node
}
function find(node: unknown, predicate: (node: ReactElement<any>) => boolean): ReactElement<any> | undefined {
  if (Array.isArray(node)) {for (const child of node) {const hit = find(child, predicate); if (hit) return hit}; return}
  if (!isValidElement<{children?: unknown}>(node)) return
  return predicate(node) ? node : find(node.props.children, predicate)
}
const image = (node: unknown) => find(node, element => element.type === SvgImage)
const button = (node: unknown, key: string) => find(node, element => element.type === IconButton && element.props.label === t(key))!
beforeEach(() => {hooks.cursor = 0; hooks.slots = []; hooks.pending = []; hooks.cleanups.clear(); state.settingsOpen = false; state.visible = true})
afterEach(() => {hooks.cleanups.forEach(cleanup => cleanup()); setLocale('en')})

it('keeps the animation source stable across language and zoom, and replays only on the replay action', () => {
  let node = render(source(10))
  const original = image(node)!.props.markup
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language); node = render(source(10))
    expect(image(node)!.props.markup).toBe(original)
    expect(image(node)!.props.replay).toBe(0)
    button(node, 'ui.components.markdown.LiveChart.80f8fbcfa011').props.onClick()
    node = render(source(10))
    expect(node.props.role).toBe('dialog')
    expect(image(node)!.props.markup).toBe(original)
    button(node, 'ui.components.markdown.LiveChart.ddde08946298').props.onClick()
  }
  node = render(source(10))
  button(node, 'ui.components.markdown.LiveChart.ad9a4e83ff36').props.onClick()
  expect(image(render(source(10)))!.props.replay).toBe(1)
})

it('defers images until stream closure and applies only the latest source after settings or page visibility resumes', () => {
  render(source(10), false)
  expect(image(render(source(10), false))).toBeUndefined()
  render(source(10))
  expect(image(render(source(10)))!.props.markup).toContain('width="10"')
  state.settingsOpen = true
  render(source(20))
  expect(image(render(source(30)))!.props.markup).toContain('width="10"')
  state.settingsOpen = false
  render(source(30))
  expect(image(render(source(30)))!.props.markup).toContain('width="30"')
  state.visible = false
  render(source(40))
  expect(image(render(source(50)))!.props.markup).toContain('width="30"')
  state.visible = true
  render(source(50))
  expect(image(render(source(50)))!.props.markup).toContain('width="50"')
})
