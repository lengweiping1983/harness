import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {McpServicePanel} from '../McpServicePanel'
import {setLocale, t} from '@/i18n'

const hooks = vi.hoisted(() => ({cursor: 0, slots: [] as any[], effects: [] as (() => void)[], cleanups: [] as (() => void)[]}))
vi.mock('react', async original => ({...await original<typeof import('react')>(),
  useRef: (value: unknown) => {const index = hooks.cursor++; return hooks.slots[index] ??= {current: value}},
  useState: (value: unknown) => {const index = hooks.cursor++; if (!(index in hooks.slots)) hooks.slots[index] = value; return [hooks.slots[index], (next: any) => {hooks.slots[index] = typeof next === 'function' ? next(hooks.slots[index]) : next}]},
  useEffect: (effect: () => void | (() => void), deps: unknown[]) => {
    const index = hooks.cursor++, previous = hooks.slots[index]
    if (!previous || deps.some((value, item) => value !== previous[item])) {
      hooks.slots[index] = deps
      hooks.effects.push(() => {const cleanup = effect(); if (cleanup) hooks.cleanups.push(cleanup)})
    }
  },
}))
vi.mock('@/i18n', async original => {const module = await original<typeof import('@/i18n')>(); return {...module, useLocale: module.getLocale}})
vi.mock('@/hooks/usePageVisible', () => ({usePageVisible: () => true}))

let saved: any, privateToken: string, writes: {path: string; body: any}[], rejectSave: boolean
const unmount = () => {for (const cleanup of hooks.cleanups) cleanup(); hooks.cleanups = []; hooks.slots = []; hooks.effects = []}
function nodes(value: any): any[] {
  if (Array.isArray(value)) return value.flatMap(nodes)
  return value && typeof value === 'object' && value.props ? [value, ...nodes(value.props.children)] : []
}
const text = (value: any): string => Array.isArray(value) ? value.map(text).join('') : value?.props ? text(value.props.children) : typeof value === 'string' ? value : ''
function render() {
  hooks.cursor = 0
  const tree = McpServicePanel()
  for (const effect of hooks.effects.splice(0)) effect()
  return tree
}
const field = (kind: string) => nodes(render()).find(node => kind === 'select' ? node.type === 'select' : node.type === 'input' && node.props.type === kind)
const button = (key: string) => nodes(render()).find(node => typeof node.type === 'function' && text(node) === t(key))
async function loaded() {render(); await vi.waitFor(() => expect(button('ui.mcpService.start').props.disabled).toBeFalsy())}

beforeEach(() => {
  unmount(); privateToken = ''; writes = []; rejectSave = false
  saved = {status: 'stopped', local_url: '', public_url: '', project_root: '', error: '', error_copy: null,
    tunnel: {mode: 'quick', public_url: '', origin_port: 18765, has_token: false, token_mask: ''}}
  vi.stubGlobal('fetch', vi.fn(async (path: string, options: RequestInit) => {
    if (options.method === 'POST') {
      const body = JSON.parse(String(options.body)); writes.push({path, body})
      if (rejectSave) return Response.json({code: 'mcp/tunnel-config', message: 'original', details: {copy: {key: 'ui.mcpService.error.configureRunning', params: {}}}}, {status: 400})
      const tunnel = path.endsWith('/start') ? body.tunnel : body
      privateToken = tunnel.token || privateToken
      saved = {...saved, status: path.endsWith('/start') ? 'starting' : 'stopped',
        tunnel: {mode: tunnel.mode, public_url: tunnel.public_url, origin_port: tunnel.origin_port, has_token: !!privateToken, token_mask: privateToken ? 'fi••••••en' : ''}}
    }
    return Response.json(saved)
  }))
})
afterEach(async () => {unmount(); vi.unstubAllGlobals(); await setLocale('en')})

it('saves settings without starting, restores them on reopen and starts using the saved token', async () => {
  await loaded()
  field('select').props.onChange({target: {value: 'named'}})
  field('url').props.onChange({target: {value: 'https://mcp.example.test'}})
  field('number').props.onChange({target: {value: '19000'}})
  field('password').props.onChange({target: {value: 'fixture-private-token'}})
  button('ui.mcpService.save').props.onClick()
  await vi.waitFor(() => expect(text(render())).toContain(t('ui.mcpService.saved')))
  expect(writes).toEqual([{path: '/api/v1/mcp-service/tunnel', body: {mode: 'named', public_url: 'https://mcp.example.test', origin_port: 19000, token: 'fixture-private-token'}}])
  expect(field('password').props.value).toBe('')
  expect(field('password').props.placeholder).toBe(saved.tunnel.token_mask)
  unmount(); await loaded()
  expect(field('select').props.value).toBe('named')
  expect(field('url').props.value).toBe('https://mcp.example.test')
  expect(field('number').props.value).toBe('19000')
  button('ui.mcpService.start').props.onClick()
  await vi.waitFor(() => expect(field('select').props.disabled ?? nodes(render()).find(node => node.type === 'fieldset').props.disabled).toBe(true))
  expect(writes[1]).toEqual({path: '/api/v1/mcp-service/start', body: {scope: 'all', tunnel: {mode: 'named', public_url: 'https://mcp.example.test', origin_port: 19000}}})
  expect(privateToken).toBe('fixture-private-token')
})

it('keeps drafts on a refused save and translates the same error without repeating the write', async () => {
  await loaded(); rejectSave = true
  field('select').props.onChange({target: {value: 'named'}})
  field('password').props.onChange({target: {value: 'draft-token'}})
  button('ui.mcpService.save').props.onClick()
  await vi.waitFor(() => expect(nodes(render()).find(node => node.props.role === 'alert')).toBeTruthy())
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    expect(text(nodes(render()).find(node => node.props.role === 'alert'))).toBe(t('ui.mcpService.error.configureRunning'))
    expect(field('password').props.value).toBe('draft-token')
  }
  expect(writes).toHaveLength(1)
})
