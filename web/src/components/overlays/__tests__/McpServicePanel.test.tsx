import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {McpServicePanel} from '../McpServicePanel'
import {McpCompanionPanel} from '../McpCompanionPanel'
import {SecretInput} from '@/components/ui/SecretInput'
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
const field = (kind: string) => nodes(render()).find(node => kind === 'select' ? node.type === 'select' : (kind === 'password' ? node.type === SecretInput && !node.props.readOnly : node.type === 'input' && node.props.type === kind))
const button = (key: string) => nodes(render()).find(node => typeof node.type === 'function' && text(node) === t(key))
async function loaded() {render(); await vi.waitFor(() => expect(button('ui.mcpService.start').props.disabled).toBeFalsy())}

beforeEach(() => {
  unmount(); privateToken = ''; writes = []; rejectSave = false
  saved = {status: 'stopped', local_url: '', public_url: '', project_root: '', error: '', error_copy: null,
    tunnel: {mode: 'quick', public_url: '', origin_port: 12345, has_token: false, token_mask: ''}}
  vi.stubGlobal('fetch', vi.fn(async (path: string, options: RequestInit) => {
    if (options.method === 'POST') {
      const body = options.body ? JSON.parse(String(options.body)) : undefined; writes.push({path, body})
      if (path.endsWith('/password')) return Response.json({password: 'fixture-password'})
      if (path.endsWith('/companion/token')) return Response.json({token: 'fixture-pairing-token'})
      if (path.endsWith('/stop')) {saved = {...saved, status: 'stopped'}; return Response.json(saved)}
      if (path.endsWith('/companion')) {
        saved = {...saved, companion: {...saved.companion, ...body, endpoint: `http://127.0.0.1:${body.port}/`, status: body.enabled ? saved.status === 'running' ? 'running' : 'stopped' : 'disabled'}}
        return Response.json(saved)
      }
      if (rejectSave) return Response.json({code: 'mcp/tunnel-config', message: 'original', details: {copy: {key: 'ui.mcpService.error.tunnelConfig', params: {}}}}, {status: 400})
      const tunnel = path.endsWith('/start') ? body.tunnel : body
      privateToken = tunnel.token || privateToken
      saved = {...saved, status: path.endsWith('/start') ? 'starting' : 'stopped',
        tunnel: {mode: tunnel.mode, public_url: tunnel.public_url, origin_port: tunnel.origin_port, has_token: !!privateToken, token_mask: privateToken ? 'fi••••••en' : ''}}
    }
    return Response.json(saved)
  }))
})
afterEach(async () => {unmount(); vi.unstubAllGlobals(); await setLocale('en')})

it('uses 12345 for a new tunnel and preserves a saved explicit origin port', async () => {
  render()
  field('select').props.onChange({target: {value: 'named'}})
  expect(field('number').props.value).toBe('12345')
  await loaded()
  expect(field('number').props.value).toBe('12345')
  unmount()
  saved.tunnel = {...saved.tunnel, mode: 'named', origin_port: 18765}
  await loaded()
  expect(field('number').props.value).toBe('18765')
  expect(writes).toEqual([])
})

it('renders the occupied origin port guidance in the current language without restarting', async () => {
  saved = {...saved, status: 'failed', error: 'original diagnostic',
    error_copy: {key: 'ui.mcpService.error.originPortBusy', params: {port: 18765}},
    tunnel: {...saved.tunnel, mode: 'named', origin_port: 18765}}
  await loaded()
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    expect(text(nodes(render()).find(node => node.props.role === 'alert')))
      .toBe(t('ui.mcpService.error.originPortBusy', {port: 18765}))
    expect(field('number').props.value).toBe('18765')
  }
  expect(writes).toEqual([])
})

it('automatically saves settings on start, restores them on reopen and reuses the saved token', async () => {
  await loaded()
  field('select').props.onChange({target: {value: 'named'}})
  field('url').props.onChange({target: {value: 'https://mcp.example.test'}})
  field('number').props.onChange({target: {value: '19000'}})
  field('password').props.onChange({target: {value: 'fixture-private-token'}})
  expect(nodes(render()).some(node => text(node) === 'Save settings')).toBe(false)
  button('ui.mcpService.start').props.onClick()
  await vi.waitFor(() => expect(field('password').props.value).toBe(''))
  expect(writes).toEqual([{path: '/api/v1/mcp-service/start', body: {scope: 'all', tunnel: {mode: 'named', public_url: 'https://mcp.example.test', origin_port: 19000, token: 'fixture-private-token'}}}])
  expect(field('password').props.value).toBe('')
  expect(field('password').props.placeholder).toBe(saved.tunnel.token_mask)
  unmount(); saved.status = 'stopped'; await loaded()
  expect(field('select').props.value).toBe('named')
  expect(field('url').props.value).toBe('https://mcp.example.test')
  expect(field('number').props.value).toBe('19000')
  button('ui.mcpService.start').props.onClick()
  await vi.waitFor(() => expect(field('select').props.disabled ?? nodes(render()).find(node => node.type === 'fieldset').props.disabled).toBe(true))
  expect(writes[1]).toEqual({path: '/api/v1/mcp-service/start', body: {scope: 'all', tunnel: {mode: 'named', public_url: 'https://mcp.example.test', origin_port: 19000}}})
  expect(privateToken).toBe('fixture-private-token')
})

it('keeps drafts on a refused start and translates the same error without repeating the write', async () => {
  await loaded(); rejectSave = true
  field('select').props.onChange({target: {value: 'named'}})
  field('url').props.onChange({target: {value: 'https://mcp.example.test'}})
  field('password').props.onChange({target: {value: 'draft-token'}})
  button('ui.mcpService.start').props.onClick()
  await vi.waitFor(() => expect(nodes(render()).find(node => node.props.role === 'alert')).toBeTruthy())
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    expect(text(nodes(render()).find(node => node.props.role === 'alert'))).toBe(t('ui.mcpService.error.tunnelConfig'))
    expect(field('password').props.value).toBe('draft-token')
  }
  expect(writes).toHaveLength(1)
})

it.each([
  ['', 'https://mcp.example.test', 'fixture-token'],
  ['0', 'https://mcp.example.test', 'fixture-token'],
  ['70000', 'https://mcp.example.test', 'fixture-token'],
  ['1.5', 'https://mcp.example.test', 'fixture-token'],
  ['19000', '', 'fixture-token'],
  ['19000', 'javascript:alert(1)', 'fixture-token'],
  ['19000', 'https://mcp.example.test/path', 'fixture-token'],
  ['19000', 'https://mcp.example.test?x=1', 'fixture-token'],
  ['19000', 'https://user:pass@mcp.example.test', 'fixture-token'],
  ['19000', 'https://mcp.example.test', ''],
])('rejects invalid tunnel settings locally: %s %s', async (port, url, token) => {
  await loaded()
  field('select').props.onChange({target: {value: 'named'}})
  field('url').props.onChange({target: {value: url}})
  field('number').props.onChange({target: {value: port}})
  field('password').props.onChange({target: {value: token}})
  button('ui.mcpService.start').props.onClick()
  expect(writes).toEqual([])
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    expect(text(nodes(render()).find(node => node.props.role === 'alert'))).toBe(t('ui.mcpService.error.tunnelConfig'))
    expect(field('number').props.value).toBe(port)
  }
})

it('reveals the MCP password through the shared input control and hides it without another request', async () => {
  saved.status = 'running'; saved.password_mask = 'fi••••rd'
  render()
  const secret = () => nodes(render()).find(node => node.type === SecretInput && node.props.readOnly)!
  await vi.waitFor(() => expect(secret()).toBeDefined())
  expect(secret().props.value).toBe('fi••••rd')
  secret().props.onToggleReveal()
  await vi.waitFor(() => expect(secret().props.revealed).toBe(true))
  expect(secret().props.value).toBe('fixture-password')
  secret().props.onToggleReveal()
  expect(secret().props.revealed).toBe(false)
  expect(secret().props.value).toBe('fi••••rd')
  expect(writes).toEqual([{path: '/api/v1/mcp-service/password', body: undefined}])
})

it('uses the same controlled input for pairing tokens and keeps unavailable reveal controls disabled', async () => {
  saved.companion = {enabled: true, port: 8877, status: 'stopped', endpoint: '', token_mask: 'pa••••en',
    executor: '', external: false, error: '', error_copy: null, extension_path: '/fixture/extension', extension_version: '1.0.0'}
  const companion = () => {
    hooks.cursor = 0
    const tree = McpCompanionPanel({state: saved, changing: false, onChange: value => {saved = value}, onBusy: () => {}})
    for (const effect of hooks.effects.splice(0)) effect()
    return tree
  }
  const secret = () => nodes(companion()).find(node => node.type === SecretInput)!
  expect(secret().props.readOnly).toBe(true)
  expect(text(companion())).toContain(t('ui.companion.tokenHint'))
  expect(secret().props.toggleDisabled).toBe(true)
  expect(writes).toHaveLength(0)
  saved.companion.status = 'running'
  expect(secret().props.toggleDisabled).toBe(false)
  secret().props.onToggleReveal()
  await vi.waitFor(() => expect(secret().props.revealed).toBe(true))
  expect(secret().props.value).toBe('fixture-pairing-token')
  secret().props.onToggleReveal()
  expect(secret().props.revealed).toBe(false)
  expect(secret().props.value).toBe('pa••••en')
  expect(writes).toEqual([{path: '/api/v1/mcp-service/companion/token', body: undefined}])
})

it('omits empty extension version parentheses in the companion settings label', async () => {
  prepareCompanion()
  saved.companion.extension_version = ''
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const label = text(companionTree())
    expect(label).toContain(t('ui.companion.extensionLabel'))
    expect(label).not.toMatch(/Extension folder \(\)|扩展目录（\）/)
  }
})

function prepareCompanion() {
  saved.companion = {enabled: true, port: 8877, status: 'stopped', endpoint: 'http://127.0.0.1:8877/', token_mask: '',
    executor: 'offline', external: false, error: '', error_copy: null, extension_path: '/fixture/extension', extension_version: '1.0.0'}
}
let pendingSave: Promise<boolean> | undefined
function companionTree() {
  hooks.cursor = 0
  const tree = McpCompanionPanel({state: saved, changing: false, onChange: value => {saved = value}, onBusy: () => {}, onSavePending: value => {pendingSave = value}})
  for (const effect of hooks.effects.splice(0)) effect()
  return tree
}
const portField = () => nodes(companionTree()).find(node => node.type === 'input' && node.props.type === 'number')!

it('saves an edited port on blur without a save button, and restores it after reopening', async () => {
  prepareCompanion(); pendingSave = undefined
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    expect(text(companionTree())).toContain(t('ui.companion.portAutoSave'))
    expect(nodes(companionTree()).some(node => ['Save port', '保存端口'].includes(text(node)))).toBe(false)
  }
  expect(writes).toHaveLength(0)
  portField().props.onChange({target: {value: '8899'}})
  expect(writes).toHaveLength(0)
  portField().props.onBlur()
  expect(await pendingSave).toBe(true)
  expect(writes).toEqual([{path: '/api/v1/mcp-service/companion', body: {enabled: true, port: 8899}}])
  expect(saved.companion.endpoint).toBe('http://127.0.0.1:8899/')
  expect(text(companionTree())).toContain(t('ui.companion.portSaved'))
  portField().props.onBlur()
  expect(writes).toHaveLength(1)
  portField().props.onChange({target: {value: '8899'}})
  portField().props.onBlur()
  expect(writes).toHaveLength(1)
  unmount(); companionTree()
  expect(portField().props.value).toBe('8899')
  expect(writes).toHaveLength(1)
})

it('rejects invalid port drafts without saving or blocking disable, and never submits during composition', async () => {
  prepareCompanion(); pendingSave = undefined
  for (const value of ['', '1000', '65536', '8877.5']) {
    portField().props.onChange({target: {value}})
    portField().props.onBlur()
    expect(text(companionTree())).toContain(t('ui.companion.invalidPort'))
    expect(writes).toHaveLength(0)
  }
  const disable = nodes(companionTree()).find(node => text(node) === t('ui.companion.disable'))!
  disable.props.onClick()
  expect(await pendingSave).toBe(true)
  expect(writes[0].body).toEqual({enabled: false, port: 8877})
  portField().props.onChange({target: {value: '9000'}})
  portField().props.onCompositionStart()
  portField().props.onBlur()
  portField().props.onCompositionEnd()
  expect(writes).toHaveLength(1)
  portField().props.onBlur()
  expect(await pendingSave).toBe(true)
  expect(writes[1].body).toEqual({enabled: false, port: 9000})
})

it.each(['network', 'mismatched', 'invalid-json'])('keeps an unknown %s autosave visible, reads status once and never automatically repeats it', async kind => {
  prepareCompanion(); pendingSave = undefined
  const transport = globalThis.fetch, calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, options: RequestInit) => {
    calls.push(path)
    const response = await transport(path, options)
    if (options.method === 'POST') {
      if (kind === 'network') throw new TypeError('lost save response')
      if (kind === 'mismatched') return Response.json({...saved, companion: {...saved.companion, port: 8877}})
      return new Response('{broken', {status: 200, headers: {'Content-Type': 'application/json'}})
    }
    return response
  }))
  portField().props.onChange({target: {value: '8899'}})
  portField().props.onBlur()
  expect(await pendingSave).toBe(false)
  expect(calls).toEqual(['/api/v1/mcp-service/companion', '/api/v1/mcp-service'])
  expect(text(companionTree())).toContain(t('ui.companion.saveUnconfirmed'))
  portField().props.onBlur()
  expect(writes).toHaveLength(1)
  expect(portField().props.value).toBe('8899')
})

it.each(['start', 'stop', 'stop-after-failed-save'])('waits for the accepted companion save before MCP %s, with one control request', async kind => {
  const action = kind === 'start' ? 'start' : 'stop'
  saved.status = action === 'start' ? 'stopped' : 'running'
  render()
  await vi.waitFor(() => {
    expect(button(`ui.mcpService.${action}`)).toBeDefined()
    expect(button(`ui.mcpService.${action}`).props.disabled).toBeFalsy()
  })
  const child = nodes(render()).find(node => node.type === McpCompanionPanel)!
  let finish!: (saved: boolean) => void
  child.props.onSavePending(new Promise<boolean>(resolve => {finish = resolve}))
  child.props.onBusy(true)
  const control = button(`ui.mcpService.${action}`)
  expect(control.props.disabled).toBeFalsy()
  control.props.onClick(); control.props.onClick()
  expect(writes).toHaveLength(0)
  child.props.onBusy(false); finish(kind !== 'stop-after-failed-save')
  await vi.waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0].path).toBe(`/api/v1/mcp-service/${action}`)
})

it('finishes an accepted port save after the panel closes without publishing into the closed instance', async () => {
  prepareCompanion(); pendingSave = undefined
  const transport = globalThis.fetch
  let release!: () => void
  const held = new Promise<void>(resolve => {release = resolve})
  vi.stubGlobal('fetch', vi.fn(async (path: string, options: RequestInit) => {
    const response = await transport(path, options)
    await held
    return response
  }))
  portField().props.onChange({target: {value: '8899'}})
  portField().props.onBlur()
  const accepted = pendingSave
  unmount(); release()
  expect(await accepted).toBe(true)
  expect(saved.companion.port).toBe(8899)
  expect(writes).toHaveLength(1)
})

it('explains the empty read-only pairing token and never fetches it before MCP starts', async () => {
  prepareCompanion()
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const field = nodes(companionTree()).find(node => node.type === SecretInput)!
    expect(field.props.readOnly).toBe(true)
    expect(field.props.value).toBe('')
    expect(field.props.placeholder).toBe(t('ui.companion.tokenPending'))
    expect(field.props['aria-description']).toBe(t('ui.companion.tokenHint'))
    expect(field.props.toggleDisabled).toBe(true)
  }
  expect(writes).toHaveLength(0)
})
