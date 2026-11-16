import {afterEach, expect, test, vi} from 'vitest'
import {type ReactElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {setLocale} from '@/i18n'
import type {RecoveryPreview, RunRecord} from '@/lib/protocol.generated'
import {RecoveryReview} from '../RecoveryReview'

// 托管同一恢复页面的钩子与副作用，子组件仍消费实际资源和预览读取逻辑。
const hooks = vi.hoisted(() => ({active: false, cursor: 0, slots: [] as any[],
  effects: [] as (() => void)[], cleanups: new Map<number, () => void>()}))
const request = vi.hoisted(() => vi.fn())
vi.mock('@/lib/http', async original => ({...await original<typeof import('@/lib/http')>(), runtimeRequest: request}))
vi.mock('@/i18n', async original => {const actual = await original<typeof import('@/i18n')>(); return {...actual, useLocale: actual.getLocale}})
vi.mock('react', async original => {
  const actual = await original<typeof import('react')>()
  return {...actual,
    useRef: (initial: unknown) => {if (!hooks.active) return actual.useRef(initial); const index = hooks.cursor++; return hooks.slots[index] ??= {current: initial}},
    useState: (initial: unknown) => {
      if (!hooks.active) return actual.useState(initial)
      const index = hooks.cursor++
      if (!(index in hooks.slots)) hooks.slots[index] = typeof initial === 'function' ? initial() : initial
      return [hooks.slots[index], (value: any) => {hooks.slots[index] = typeof value === 'function' ? value(hooks.slots[index]) : value}]
    },
    useEffect: (effect: () => void | (() => void), deps: unknown[]) => {
      if (!hooks.active) return actual.useEffect(effect, deps)
      const index = hooks.cursor++, previous = hooks.slots[index]
      if (!previous || deps.some((value, offset) => value !== previous[offset])) {
        hooks.slots[index] = deps
        hooks.effects.push(() => {hooks.cleanups.get(index)?.(); const cleanup = effect(); if (cleanup) hooks.cleanups.set(index, cleanup)})
      }
    },
  }
})

afterEach(async () => {
  hooks.cleanups.forEach(cleanup => cleanup()); hooks.cleanups.clear()
  hooks.slots = []; hooks.effects = []; hooks.active = false; request.mockReset()
  await setLocale('zh-CN')
})

test('the same recovery instance translates inspection rejection without refetching or continuing', async () => {
  const record: RunRecord = {id: 'inspection-run', session_id: 'inspection-conversation', text: 'Sports animation',
    status: 'failed', created_at: 1, error: {code: 'llm/provider', message: 'Original provider diagnostic',
      details: {...{error_code: 'data_inspection_failed'}, copy: undefined}}}
  const preview: RecoveryPreview = {run_id: record.id, session_id: record.session_id, status: 'failed', turn: 1,
    task: record.text, task_truncated: false, error: 'Original provider diagnostic', scope_known: true,
    version: 'inspection-version', tools: [], total_tools: 0, uncertain_operations: 0,
    verification_required: false, next_offset: null, continuation_text: 'Continue 原文',
    inspection_text: 'Inspect 原文', known_continuations: {}}
  request.mockResolvedValue(preview)
  const onSubmitted = vi.fn(), onOriginal = vi.fn(), onClose = vi.fn()
  const props = {sessionId: record.session_id, record, onSubmitted, onOriginal, onClose}
  const before = JSON.stringify({record, preview})
  function render() {
    hooks.cursor = 0; hooks.active = true
    let tree: ReactElement<{children: React.ReactNode}>
    try {tree = RecoveryReview(props)} finally {hooks.active = false}
    while (hooks.effects.length) hooks.effects.shift()!()
    return renderToStaticMarkup(<>{tree.props.children}</>)
  }
  render()
  await vi.waitFor(() => expect(hooks.slots[0]).toEqual(preview))
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const html = render()
    expect(html).toContain(language === 'zh-CN' ? '模型内容审核未通过' : 'Model content review rejected')
    expect(html).toContain(language === 'zh-CN' ? '请求及上下文' : 'request and context')
    expect(html).toContain('Original provider diagnostic')
    expect(html).toContain('Inspect 原文')
  }
  expect(request).toHaveBeenCalledTimes(1)
  expect(request.mock.calls[0].slice(0, 3)).toEqual([
    '/sessions/inspection-conversation/runs/inspection-run/recovery', 'GET', undefined])
  expect(onSubmitted).not.toHaveBeenCalled(); expect(onOriginal).not.toHaveBeenCalled(); expect(onClose).not.toHaveBeenCalled()
  expect(JSON.stringify({record, preview})).toBe(before)
})
