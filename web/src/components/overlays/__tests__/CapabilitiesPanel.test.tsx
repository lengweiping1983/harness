import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {CapabilitiesPanel, NumberField} from '../CapabilitiesPanel'
import {setLocale, t} from '@/i18n'

type Scope = {cursor: number; slots: any[]; effects: (() => void)[]; cleanups: (() => void)[]}
const hooks = vi.hoisted(() => ({current: null as unknown as Scope}))
vi.mock('react', async original => {
  const actual = await original<typeof import('react')>()
  const effect = (callback: () => void | (() => void), deps: unknown[]) => {
    const scope = hooks.current, index = scope.cursor++, previous = scope.slots[index]
    if (!previous || deps.some((value, i) => value !== previous[i])) {
      scope.slots[index] = deps
      scope.effects.push(() => {const cleanup = callback(); if (cleanup) scope.cleanups.push(cleanup)})
    }
  }
  return {...actual, useEffect: effect, useLayoutEffect: effect,
    useRef: (value: unknown) => {const scope = hooks.current, index = scope.cursor++; return scope.slots[index] ??= {current: value}},
    useState: (value: unknown) => {
      const scope = hooks.current, index = scope.cursor++
      if (!(index in scope.slots)) scope.slots[index] = value
      return [scope.slots[index], (next: unknown) => {scope.slots[index] = typeof next === 'function' ? next(scope.slots[index]) : next}]
    },
  }
})
vi.mock('@/i18n', async original => {const actual = await original<typeof import('@/i18n')>(); return {...actual, useLocale: actual.getLocale}})
vi.mock('@/store/app', () => ({useAppStore: (selector: any) => selector({toast: vi.fn()})}))
const scopes: Scope[] = []
const scope = () => {const value = {cursor: 0, slots: [], effects: [], cleanups: []}; scopes.push(value); return value}
const nodes = (value: any): any[] => Array.isArray(value) ? value.flatMap(nodes) : value?.props ? [value, ...nodes(value.props.children)] : []
const text = (value: any): string => Array.isArray(value) ? value.map(text).join('') : value?.props ? text(value.props.children) : typeof value === 'string' ? value : ''
function render(owner: Scope, component: any, props?: any) {
  hooks.current = owner; owner.cursor = 0
  const tree = component(props)
  for (const effect of owner.effects.splice(0)) effect()
  return tree
}
let config: any, writes: any[]
beforeEach(() => {
  config = {permission_policy: 'full_access', max_steps: 10, token_budget: 1000, network_timeout: 180,
    context_auto_manage: true, context_working_budget: 12000, summary_max_tokens: 8192, output_reserve: 16000,
    context_window: null, tool_result_max_chars: 12000, tool_schema_mode: 'auto'}; writes = []
  vi.stubGlobal('fetch', vi.fn(async (_path, options) => {
    if (options.method === 'PATCH') {const body = JSON.parse(options.body); writes.push(body); config = {...config, ...body}}
    return Response.json({config, effective: {}})
  }))
})
afterEach(() => {for (const owner of scopes.splice(0)) for (const cleanup of owner.cleanups) cleanup(); vi.unstubAllGlobals(); setLocale('en')})

it.each(['max_steps', 'token_budget', 'network_timeout'] as const)('validates %s before PATCH, keeps invalid drafts and preserves empty-value semantics', async key => {
  const parent = scope(), child = scope()
  const panel = () => render(parent, CapabilitiesPanel)
  const props = () => nodes(panel()).find(node => node.type === NumberField && node.key === key)!.props
  panel()
  await vi.waitFor(() => expect(nodes(panel()).filter(node => node.type === NumberField)).toHaveLength(3))
  const field = () => render(child, NumberField, props())
  const input = () => nodes(field()).find(node => node.type === 'input')!
  for (const raw of ['-5', '1.5', '0', 'Infinity', '9007199254740992', ...(key === 'network_timeout' ? [''] : [])]) {
    input().props.onChange({target: {value: raw}})
    await input().props.onBlur({target: {value: raw}})
    expect(writes).toEqual([])
    for (const locale of ['en', 'zh-CN', 'en'] as const) {
      await setLocale(locale)
      expect(input().props.value).toBe(raw)
      expect(input().props['aria-invalid']).toBe(true)
      expect(text(nodes(field()).find(node => node.props.role === 'alert'))).toBe(t(key === 'network_timeout' ? 'ui.settings.positiveInteger' : 'ui.settings.optionalPositiveInteger'))
    }
  }
  const valid = key === 'network_timeout' ? '240' : ''
  input().props.onChange({target: {value: valid}})
  await input().props.onBlur({target: {value: valid}})
  expect(writes).toEqual([{[key]: valid === '' ? null : 240}])
  expect(input().props['aria-invalid']).toBe(false)
})
