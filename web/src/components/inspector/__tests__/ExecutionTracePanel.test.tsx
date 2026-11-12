import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import type {ReactElement} from 'react'
import {ExecutionTracePanel} from '../ExecutionTracePanel'
import {RequestNumberBadge} from '../RequestNumberBadge'
import {RefreshButton, ShowMoreControl} from '@/components/ui/primitives'
import {api} from '@/lib/api'
import type {ExecutionTracePage, TraceEntry} from '@/lib/executionTrace'
import {noticeText} from '@/lib/noticeText'
import {setLocale, t} from '@/i18n'

const hooks = vi.hoisted(() => ({cursor: 0, slots: [] as any[], pending: [] as (() => void)[], cleanups: new Map<number, () => void>()}))
const visibility = vi.hoisted(() => ({visible: true}))
vi.mock('react', async original => ({...await original<typeof import('react')>(),
  useRef: (value: unknown) => {const index = hooks.cursor++; return hooks.slots[index] ??= {current: value}},
  useState: (value: unknown) => {
    const index = hooks.cursor++
    if (!(index in hooks.slots)) hooks.slots[index] = value
    return [hooks.slots[index], (next: any) => {hooks.slots[index] = typeof next === 'function' ? next(hooks.slots[index]) : next}]
  },
  useEffect: (run: () => void | (() => void), deps: unknown[]) => {
    const index = hooks.cursor++, previous = hooks.slots[index] as unknown[] | undefined
    if (!previous || deps.some((dep, position) => !Object.is(dep, previous[position]))) {
      hooks.slots[index] = deps
      hooks.pending.push(() => {
        hooks.cleanups.get(index)?.(); hooks.cleanups.delete(index)
        const cleanup = run(); if (cleanup) hooks.cleanups.set(index, cleanup)
      })
    }
  },
}))
vi.mock('@/i18n', async original => {const actual = await original<typeof import('@/i18n')>(); return {...actual, useLocale: actual.getLocale}})
vi.mock('@/hooks/usePageVisible', () => ({usePageVisible: () => visibility.visible}))

let props: Parameters<typeof ExecutionTracePanel>[0]
let timers: Set<() => void>
function render() {
  hooks.cursor = 0
  const tree = ExecutionTracePanel(props)
  hooks.pending.splice(0).forEach(run => run())
  return tree
}
function elements(node: unknown): ReactElement<any>[] {
  if (!node || typeof node !== 'object') return []
  if (Array.isArray(node)) return node.flatMap(elements)
  const element = node as ReactElement<any>
  return [element, ...elements(element.props?.children)]
}
const hours = () => elements(render()).filter(element => element.props['data-trace-hour'])
const entries = (node: unknown) => elements(node).filter(element => element.props['data-trace-id'])
const text = (node: unknown): string => typeof node === 'string' || typeof node === 'number' ? String(node)
  : Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object' ? text((node as ReactElement<any>).props?.children) : ''
const at = (hour: number, minute = 0, second = 0) => new Date(2026, 10, 6, hour, minute, second).getTime()
const row = (index: number, time: number): TraceEntry => ({id: 'entry-' + index, kind: index % 5 ? 'tool' : 'request', time, order: index,
  name: 'Original name 原文', status: 'completed', ...(index % 5 ? {} : {request: {id: 'trace-' + index} as TraceEntry['request']})})
const rows = Array.from({length: 120}, (_, index) => row(index, index < 110 ? at(10, 0, index) : at(11, 0, index - 110)))
function page(entries: TraceEntry[], more = false, after: string | null = null, session = 'owner'): ExecutionTracePage {
  return {entries, has_more: more, next_after: after, context_missing: false, session_id: session}
}
function fixture() {
  return vi.spyOn(api, 'executionTrace').mockImplementation(async (_session, _agent, after) => ({ok: true, data:
    after === undefined ? page(rows.slice(0, 50), true, 'first')
      : after === 'first' ? page(rows.slice(50, 100), true, 'second') : page(rows.slice(100))}))
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(complete => {resolve = complete})
  return {promise, resolve}
}

beforeEach(() => {
  hooks.cursor = 0; hooks.slots = []; hooks.pending = []; hooks.cleanups.clear()
  visibility.visible = true; props = {sessionId: 'owner'}; timers = new Set()
  vi.stubGlobal('window', {setInterval: (callback: () => void) => {timers.add(callback); return callback}, clearInterval: (callback: () => void) => timers.delete(callback)})
})
afterEach(() => {
  Array.from(hooks.cleanups.values()).reverse().forEach(cleanup => cleanup())
  vi.restoreAllMocks(); vi.unstubAllGlobals(); setLocale('en')
})

it('labels title-generation requests distinctly through English Chinese English without rereading', async () => {
  const title = {...row(0, at(10)), request: {id: 'title-trace', purpose: 'title'} as TraceEntry['request']}
  const reads = vi.spyOn(api, 'executionTrace').mockResolvedValue({ok: true, data: page([title])})
  render()
  await vi.waitFor(() => expect(hours()).toHaveLength(1))
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    expect(text(render())).toContain(t('ui.trace.titleRequest'))
    expect(entries(render())[0].props['data-trace-id']).toBe(title.id)
  }
  expect(reads).toHaveBeenCalledTimes(1)
})

it('首次读取跨过多页后显示 10 点和 11 点两组，组内展开不再影响其他小时发现', async () => {
  const reads = fixture()
  render()
  await vi.waitFor(() => expect(hours()).toHaveLength(2))
  expect(reads.mock.calls.map(call => call[2])).toEqual([undefined, 'first', 'second'])
  const [ten, eleven] = hours()
  expect(text(ten.props.children[0])).toContain('10:00–10:59')
  expect(text(eleven.props.children[0])).toContain('11:00–11:59')
  expect(entries(ten)).toHaveLength(10)
  expect(entries(eleven)).toHaveLength(10)
  expect(elements(eleven).find(element => element.type === RequestNumberBadge)?.props.number).toBe(23)
  elements(ten).find(element => element.type === ShowMoreControl)!.props.onClick()
  expect(entries(hours()[0])).toHaveLength(20)
  expect(reads).toHaveBeenCalledTimes(3)
  hours()[0].props.onToggle({currentTarget: {open: false}})
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    expect(hours()).toHaveLength(2)
    expect(hours()[0].props.open).toBe(false)
    expect(entries(hours()[0])).toHaveLength(20)
    expect(text(render())).toContain(t('ui.trace.main'))
  }
  expect(reads).toHaveBeenCalledTimes(3)
})

it('刷新读取后续页面和新小时，并保留已收起分组及组内展开数量', async () => {
  const reads = fixture()
  render()
  await vi.waitFor(() => expect(hours()).toHaveLength(2))
  elements(hours()[0]).find(element => element.type === ShowMoreControl)!.props.onClick()
  hours()[0].props.onToggle({currentTarget: {open: false}})
  reads.mockImplementation(async (_session, _agent, after) => ({ok: true, data: after === undefined
    ? page(rows.slice(0, 50), true, 'first') : after === 'first'
      ? page(rows.slice(50, 100), true, 'second') : page([...rows.slice(100), row(120, at(12))])}))
  timers.forEach(run => run())
  await vi.waitFor(() => expect(hours()).toHaveLength(3))
  expect(hours()[0].props.open).toBe(false)
  expect(entries(hours()[0])).toHaveLength(20)
  expect(text(hours()[2].props.children[0])).toContain('12:00–12:59')
})

it('显示更多位于小时内的日志列表末尾，与请求条目使用同一缩进层级', async () => {
  const reads = fixture()
  render()
  await vi.waitFor(() => expect(hours()).toHaveLength(2))
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const [ten, eleven] = hours()
    const list = elements(ten).find(element => element.type === 'ol')!
    const items = elements(list).filter(element => element.type === 'li')
    expect(list.props.className.split(' ')).toEqual(expect.arrayContaining(['ml-3', 'pl-4']))
    expect(items).toHaveLength(11)
    expect(items.slice(0, 10).every(element => element.props['data-trace-id'])).toBe(true)
    expect(items.at(-1)!.props.children.type).toBe(ShowMoreControl)
    expect(ten.props.children.flat().some((element: ReactElement<any>) => element?.type === ShowMoreControl)).toBe(false)
    expect(elements(eleven).some(element => element.type === ShowMoreControl)).toBe(false)
  }
  expect(reads).toHaveBeenCalledTimes(3)
})

it('跨日相同小时分别分组，空日志保持空态且不继续读取', async () => {
  const reads = vi.spyOn(api, 'executionTrace').mockResolvedValue({ok: true, data: page([
    row(1, at(10)), row(2, new Date(2026, 10, 7, 10).getTime()),
  ])})
  render()
  await vi.waitFor(() => expect(hours()).toHaveLength(2))
  expect(hours()[0].props['data-trace-hour']).not.toBe(hours()[1].props['data-trace-hour'])
  reads.mockResolvedValue({ok: true, data: page([])})
  timers.forEach(run => run())
  await vi.waitFor(() => expect(hours()).toHaveLength(0))
  expect(text(render())).toContain(t('ui.trace.empty'))
  expect(reads).toHaveBeenCalledTimes(2)
})

it('子智能体层级只读取并展示所属对话的分页，保留上下文不可用提示', async () => {
  props = {sessionId: 'owner', agentSessionId: 'child'}
  const reads = vi.spyOn(api, 'executionTrace').mockImplementation(async (_session, _agent, after) => ({ok: true, data: {
    ...page(after ? [row(2, at(11))] : [row(1, at(10))], !after, after ? null : 'child-next', 'child'),
    context_missing: true,
  }}))
  render()
  await vi.waitFor(() => expect(hours()).toHaveLength(2))
  expect(reads.mock.calls.map(call => call.slice(0, 3))).toEqual([['owner', 'child', undefined], ['owner', 'child', 'child-next']])
  expect(text(render())).toContain(t('ui.trace.missing'))
})

it('后续页失败保留已确认的所有小时，错误随语言变化且显式刷新可恢复', async () => {
  const reads = fixture()
  render()
  await vi.waitFor(() => expect(hours()).toHaveLength(2))
  reads.mockImplementation(async (_session, _agent, after) => after === undefined
    ? {ok: true, data: page(rows.slice(0, 50), true, 'first')}
    : {ok: false, message: 'Original diagnostic', code: 'fixture/failure', status: 500, copy: {key: 'ui.http.invalidResponse'}})
  timers.forEach(run => run())
  await vi.waitFor(() => expect(elements(render()).some(element => element.props.role === 'alert')).toBe(true))
  expect(hours()).toHaveLength(2)
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    expect(text(elements(render()).find(element => element.props.role === 'alert'))).toBe(noticeText({key: 'ui.http.invalidResponse'}))
  }
  expect(reads).toHaveBeenCalledTimes(5)
  reads.mockResolvedValue({ok: true, data: page(rows)})
  elements(render()).find(element => element.type === RefreshButton)!.props.onClick()
  render()
  await vi.waitFor(() => expect(elements(render()).some(element => element.props.role === 'alert')).toBe(false))
  expect(hours()).toHaveLength(2)
})

it.each(['cursor', 'empty', 'duplicate', 'owner', 'throw'])('拒绝无进展或错配分页：%s', async invalid => {
  const reads = vi.spyOn(api, 'executionTrace').mockResolvedValueOnce({ok: true, data: page(rows.slice(0, 50), true, 'first')})
  if (invalid === 'throw') reads.mockRejectedValue(new Error('Original read error'))
  else reads.mockResolvedValue({ok: true, data: invalid === 'cursor' ? page(rows.slice(50, 100), true, 'first')
    : invalid === 'empty' ? page([], true, 'second') : invalid === 'duplicate' ? page(rows.slice(0, 50)) : page(rows.slice(50), false, null, 'other')})
  render()
  await vi.waitFor(() => expect(elements(render()).some(element => element.props.role === 'alert')).toBe(true))
  expect(hours()).toHaveLength(0)
  expect(reads).toHaveBeenCalledTimes(2)
  expect(elements(render()).find(element => element.type === RefreshButton)?.props.disabled).toBe(false)
})

it.each(['hidden', 'inactive', 'session', 'unmount'])('跨页读取在 %s 后停止，迟到页面不写回或继续翻页', async change => {
  const pending = deferred<Awaited<ReturnType<typeof api.executionTrace>>>()
  const reads = vi.spyOn(api, 'executionTrace').mockResolvedValueOnce({ok: true, data: page(rows.slice(0, 50), true, 'first')})
    .mockImplementationOnce(() => pending.promise).mockResolvedValue({ok: true, data: page([], false, null, 'other')})
  render()
  await vi.waitFor(() => expect(reads).toHaveBeenCalledTimes(2))
  timers.forEach(run => run())
  expect(reads).toHaveBeenCalledTimes(2)
  if (change === 'hidden') visibility.visible = false
  if (change === 'inactive') props = {...props, active: false}
  if (change === 'session') props = {sessionId: 'other'}
  if (change === 'unmount') Array.from(hooks.cleanups.values()).reverse().forEach(cleanup => cleanup())
  else render()
  expect(reads.mock.calls[1][3]?.aborted).toBe(true)
  pending.resolve({ok: true, data: page(rows.slice(50, 100), true, 'second')})
  await pending.promise
  await Promise.resolve()
  expect(reads).toHaveBeenCalledTimes(change === 'session' ? 3 : 2)
  expect(hours()).toHaveLength(0)
})
