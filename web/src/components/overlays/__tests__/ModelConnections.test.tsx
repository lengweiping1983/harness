import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {isValidElement, type ReactElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {setLocale, t} from '@/i18n'
import {ModelConnections, ConnectionEditor, ProviderModelPreferences, RemovalConfirmation} from '../ModelConnections'
import {ProviderModelFields} from '../ProviderModelEditor'
import {Button} from '@/components/ui/primitives'
import type {ModelProvider, ProviderSnapshot, ProviderTemplate} from '@/lib/providerConnections'
import type {ModelInfo} from '@/types'
import {ResourceError} from '@/lib/resourceError'

// 只托管被测页面的钩子；子组件仍通过 React 服务端渲染验证实际资源消费。
const hooks = vi.hoisted(() => ({active: false, cursor: 0, slots: [] as any[], effects: [] as (() => void)[], cleanups: new Map<number, () => void>()}))
vi.mock('react', async original => {
  const actual = await original<typeof import('react')>()
  return {...actual,
    useRef: (initial: unknown) => {if (!hooks.active) return actual.useRef(initial); const index = hooks.cursor++; return hooks.slots[index] ??= {current: initial}},
    useState: (initial: unknown) => {
      if (!hooks.active) return actual.useState(initial)
      const index = hooks.cursor++
      if (!(index in hooks.slots)) hooks.slots[index] = typeof initial === 'function' ? initial() : initial
      const slots = hooks.slots
      return [slots[index], (value: any) => {slots[index] = typeof value === 'function' ? value(slots[index]) : value}]
    },
    useEffect: (effect: () => void | (() => void), deps: unknown[]) => {
      if (!hooks.active) return actual.useEffect(effect, deps)
      const index = hooks.cursor++, previous = hooks.slots[index]
      if (!previous || deps.some((value, offset) => value !== previous[offset])) {
        hooks.slots[index] = deps
        hooks.effects.push(() => {hooks.cleanups.get(index)?.(); hooks.cleanups.delete(index); const cleanup = effect(); if (cleanup) hooks.cleanups.set(index, cleanup)})
      }
    },
  }
})
const fixture = vi.hoisted(() => ({visible: true, state: {} as Record<string, any>, request: vi.fn(), changed: vi.fn()}))
vi.mock('@/store/app', () => ({modelSaveKey: () => 'default', useAppStore: Object.assign((select: (value: any) => unknown) => select(fixture.state), {setState: (next: object) => Object.assign(fixture.state, next)})}))
vi.mock('@/hooks/usePageVisible', () => ({usePageVisible: () => fixture.visible}))
vi.mock('@/lib/http', async original => ({...await original<typeof import('@/lib/http')>(), runtimeRequest: fixture.request}))
vi.mock('@/lib/connectionRoutes', () => ({connectionChanged: fixture.changed}))
vi.mock('@/i18n', async original => {const actual = await original<typeof import('@/i18n')>(); return {...actual, useLocale: actual.getLocale}})

const provider = (id: string): ModelProvider => ({id, type: 'custom', name: 'Custom 原文', connections: ['one', 'two'].map(connectionId => ({
  id: connectionId, name: 'Gateway 原文', protocol: 'openai-completions', base_url: 'https://example.test/v1', enabled: true,
  auth: {type: 'api_key', env: {}}, keys: [{id: 'secret', mask: '******1234', enabled: true}],
  models: ['first', 'second'].map(modelId => ({id: modelId, model: `remote-${modelId}`, name: 'Model 原文', enabled: true, input: ['text'], context_window: 12000})),
}))})
const template: ProviderTemplate = {id: 'custom', name: 'Custom API', api_key: true, oauth: false, base_url: '', env: [], models: []}
let routing: ProviderSnapshot
const catalog = (providers: ModelProvider[]): ModelInfo[] => providers.flatMap(provider => provider.connections.flatMap(connection => connection.models.map(model => ({id: `harness/${provider.id}/${connection.id}/${model.id}`, label: model.name, context_window: model.context_window, provider: provider.name}))))
const focus = vi.fn(), scroll = vi.fn(), formFocus = vi.fn(), formScroll = vi.fn(), validity = vi.fn(() => true)
const row = {dataset: {modelId: 'harness/a/two/second'}, scrollIntoView: scroll, querySelector: () => ({focus})}
const card = {dataset: {providerId: 'a'}, scrollIntoView: scroll, querySelector: () => ({focus}), querySelectorAll: () => [row]}
const formElement = {scrollIntoView: formScroll, reportValidity: validity, querySelector: () => ({focus: formFocus})}

function nodes(node: unknown): ReactElement<any>[] {
  if (Array.isArray(node)) return node.flatMap(nodes)
  if (!isValidElement<any>(node)) return []
  return [node, ...nodes(node.props.children)]
}
function render() {
  hooks.cursor = 0; hooks.active = true
  let tree: ReactElement
  try {tree = ModelConnections()} finally {hooks.active = false}
  const root = tree as ReactElement & {ref: {current: unknown}}
  root.ref.current = {querySelectorAll: () => [card]}
  nodes(tree).filter(node => node.type === ProviderModelPreferences).forEach(node => node.props.modelElements.set('harness/a/two/second', row))
  const form = nodes(tree).find(node => node.type === 'form') as (ReactElement & {ref: {current: unknown}}) | undefined
  if (form) form.ref.current = formElement
  while (hooks.effects.length) hooks.effects.shift()!()
  return tree
}
function list(id = 'a') {return nodes(render()).find(node => node.type === ProviderModelPreferences && node.props.provider.id === id)!}
function modelActions(id = 'a', connectionId = 'two', modelId = 'second') {
  const entry = list(id), connection = entry.props.provider.connections.find((row: any) => row.id === connectionId)
  return nodes(entry.props.renderActions(connection, connection.models.find((row: any) => row.id === modelId)))
}
function editModel() {modelActions().find(node => node.props['data-edit-action'])!.props.onClick()}
function feedback(id = 'a', connectionId = 'two', modelId = 'second') {
  const entry = list(id), connection = entry.props.provider.connections.find((row: any) => row.id === connectionId)
  const confirmation = entry.props.renderFeedback(connection, connection.models.find((row: any) => row.id === modelId))
  return confirmation ? RemovalConfirmation(confirmation.props) : null
}
function providerCard(id = 'a') {return nodes(render()).find(node => node.props['data-provider-id'] === id)!}
function providerConfirmation(id = 'a') {
  const confirmation = nodes(providerCard(id)).find(node => node.type === RemovalConfirmation)
  return confirmation ? RemovalConfirmation(confirmation.props) : null
}
function button(tree: unknown, key: string) {return nodes(tree).find(node => node.type === Button && node.props.children === t(key))!}
function fields() {return nodes(render()).find(node => node.type === ProviderModelFields)!}
function unmount() {hooks.cleanups.forEach(cleanup => cleanup()); hooks.cleanups.clear()}
function deferred<T>() {let resolve!: (value: T) => void, reject!: (value: unknown) => void; const promise = new Promise<T>((done, fail) => {resolve = done; reject = fail}); return {promise, resolve, reject}}
const writes = () => fixture.request.mock.calls.filter(call => call[1] === 'PATCH')
async function loaded() {render(); await vi.waitFor(() => expect(list()).toBeDefined())}

beforeEach(async () => {
  hooks.slots = []; hooks.cursor = 0; hooks.effects = []; hooks.cleanups.clear(); fixture.visible = true
  routing = {version: 2, revision: 'original', providers: [provider('b'), provider('a')]}
  fixture.state = {models: catalog(routing.providers), modelVisibility: {}, modelVisibilitySaves: {}, modelSaves: {}, defaultModelSave: null, defaultModel: 'harness/a/two/second'}
  fixture.request.mockReset(); fixture.changed.mockReset(); focus.mockClear(); scroll.mockClear(); formFocus.mockClear(); formScroll.mockClear(); validity.mockReturnValue(true)
  fixture.request.mockImplementation(async (path: string, method = 'GET', body?: any) => {
    if (path === '/connection/providers') return {providers: [template]}
    if (path === '/codex-gateway') return {accounts: []}
    if (path === '/models') return {models: catalog(routing.providers), active: fixture.state.defaultModel}
    if (path === '/connection' && method === 'GET') return routing
    if (path === '/connection' && method === 'PATCH') {routing = {...routing, revision: 'saved', providers: body.providers}; return {routing}}
    throw new Error(`Unexpected request: ${method} ${path}`)
  })
  await setLocale('en')
})
afterEach(async () => {unmount(); await setLocale('zh-CN')})

it('reads the model catalog on opening instead of depending on a populated global cache', async () => {
  fixture.state.models = []
  await loaded()
  expect(fixture.request.mock.calls.filter(call => call[0] === '/models')).toHaveLength(1)
  expect(fixture.state.models).toEqual(catalog(routing.providers))
  expect(renderToStaticMarkup(render())).toContain('Model 原文')
  expect(writes()).toHaveLength(0)
})

it('retains configured model editing and removal when the model catalog read fails', async () => {
  fixture.state.models = []
  const transport = fixture.request.getMockImplementation()!
  fixture.request.mockImplementation((path, ...args) => path === '/models' ? Promise.reject(new ResourceError({key: 'ui.http.invalidResponse'})) : transport(path, ...args))
  await loaded()
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const html = renderToStaticMarkup(render())
    expect(html).toContain('Model 原文')
    expect(html).toContain(t('ui.http.invalidResponse'))
    expect(html).not.toContain(t('ui.providers.empty'))
  }
  expect(writes()).toHaveLength(0)
  modelActions().find(node => node.props['aria-label'] === t('ui.providers.removeModelNamed', {name: 'Model 原文'}))!.props.onClick()
  expect(feedback()).not.toBeNull()
  button(feedback(), 'ui.providers.cancel').props.onClick()
  editModel()
  expect(fields().props.value.id).toBe('second')
  expect(fixture.request.mock.calls.filter(call => call[0] === '/models')).toHaveLength(1)
  expect(writes()).toHaveLength(0)
})

it('retries a failed catalog read without writing and re-enables the actual catalog entries', async () => {
  fixture.state.models = []
  const transport = fixture.request.getMockImplementation()!
  let failed = true
  fixture.request.mockImplementation((path, ...args) => path === '/models' && failed ? Promise.reject(new Error('Catalog diagnostic 原文')) : transport(path, ...args))
  await loaded()
  expect(renderToStaticMarkup(render())).toContain(t('ui.providers.modelNotInCatalog'))
  failed = false
  button(render(), 'ui.providers.retryLoading').props.onClick()
  render()
  await vi.waitFor(() => expect(fixture.state.models).toEqual(catalog(routing.providers)))
  const html = renderToStaticMarkup(render())
  expect(html).toContain('Model 原文')
  expect(html).not.toContain('Catalog diagnostic 原文')
  expect(html).not.toContain(t('ui.providers.modelNotInCatalog'))
  expect(fixture.request.mock.calls.filter(call => call[0] === '/models')).toHaveLength(2)
  expect(writes()).toHaveLength(0)
})

it('opens only the chosen model, preserves its draft through English Chinese English, and saves the owning identity once', async () => {
  await loaded()
  const original = structuredClone(routing)
  editModel()
  expect(formScroll).not.toHaveBeenCalled()
  let tree = render()
  expect(formScroll).toHaveBeenCalledWith({block: 'start'})
  expect(formFocus).toHaveBeenCalledWith({preventScroll: true})
  const edited = {...fields().props.value, name: 'Edited 原文', context_window: 999000, max_output_tokens: 32000, input: ['text', 'image'], reasoning_efforts: {off: null, high: 'high'}}
  fields().props.onChange(edited)
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale); tree = render()
    const html = renderToStaticMarkup(tree)
    expect(html).toContain(t('ui.providers.editModel'))
    expect(html).toContain(t('ui.providers.modelEditContext', {provider: 'Custom 原文', model: 'Edited 原文'}))
    expect(html).not.toContain(t('ui.providers.supplierName'))
    expect(html).not.toContain(t('ui.providers.authentication'))
    expect(nodes(tree).filter(node => node.type === ProviderModelPreferences || node.type === ConnectionEditor)).toHaveLength(0)
    expect(fields().props.value).toEqual(edited)
    expect(button(tree, 'ui.providers.save').props.variant).toBe('primary')
  }
  const write = deferred<{routing: ProviderSnapshot}>()
  fixture.request.mockImplementationOnce(() => write.promise)
  const save = button(render(), 'ui.providers.save')
  const pending = save.props.onClick()
  await save.props.onClick()
  const saving = render()
  expect(button(saving, 'ui.providers.save').props.disabled).toBe(true)
  expect(button(saving, 'ui.providers.cancel').props.disabled).toBe(true)
  expect(fields().props.disabled).toBe(true)
  expect(writes()).toHaveLength(1)
  const payload = writes()[0][2]
  expect(payload.revision).toBe('original')
  expect(payload.providers.map((row: ModelProvider) => row.id)).toEqual(['b', 'a'])
  expect(payload.providers[1].connections[1].models[1]).toEqual(edited)
  expect(payload.providers[1].connections[0].models).toEqual(original.providers[1].connections[0].models)
  expect(payload.providers[0].connections[1].models[1]).toEqual(original.providers[0].connections[1].models[1])
  expect(payload.providers[1].connections[1].keys).toEqual([{id: 'secret', enabled: true}])
  expect(routing).toEqual(original)
  write.resolve({routing: {...routing, revision: 'saved', providers: payload.providers}}); await pending
  tree = render()
  expect(nodes(tree).some(node => node.type === 'form')).toBe(false)
  expect(fixture.changed).toHaveBeenCalledTimes(1)
  expect(focus).toHaveBeenCalledWith({preventScroll: true})
})

it('puts the provider save toolbar before every field and restores the source on cancel without a write', async () => {
  await loaded()
  button(render(), 'ui.providers.edit').props.onClick()
  const tree = render(), all = nodes(tree), header = all.find(node => node.type === 'header')!
  expect(header.props.className).toContain('sticky top-0')
  expect(button(header, 'ui.providers.save')).toBeDefined()
  expect(all.indexOf(header)).toBeLessThan(all.findIndex(node => node.type === 'input'))
  expect(all.filter(node => node.type === ProviderModelPreferences)).toHaveLength(0)
  expect(all.filter(node => node.type === ConnectionEditor)).toHaveLength(2)
  button(tree, 'ui.providers.cancel').props.onClick(); render()
  expect(writes()).toHaveLength(0)
  expect(scroll).toHaveBeenCalledWith({block: 'nearest'})
  expect(focus).toHaveBeenCalledWith({preventScroll: true})
  editModel(); render(); fields().props.onChange({...fields().props.value, name: 'Discard 原文'})
  button(render(), 'ui.providers.cancel').props.onClick(); render(); editModel()
  expect(fields().props.value.name).toBe('Model 原文')
  expect(writes()).toHaveLength(0)
})

it('uses the shared inline visibility control for provider keys and secret environment values', async () => {
  await loaded()
  button(render(), 'ui.providers.edit').props.onClick()
  const entry = nodes(render()).find(node => node.type === ConnectionEditor)!
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const html = renderToStaticMarkup(entry)
    expect(html).toContain(`aria-label="${t('ui.providers.showKey')}"`)
    expect(html).toContain('absolute inset-y-1 right-1')
    expect(html).toContain('pr-11')
    const env = renderToStaticMarkup(<ConnectionEditor {...entry.props} connection={{...entry.props.connection,
      auth: {type: 'environment', env: {API_TOKEN: 'fixture-token', REGION: 'fixture-region'}}}}
      template={{...entry.props.template, env: ['API_TOKEN', 'REGION']}} />)
    expect(env).toContain(`aria-label="${t('ui.secrets.show')}"`)
    expect(env).toContain('type="password"')
    expect(env).toContain('value="fixture-token"')
    expect(env).toContain('value="fixture-region"')
  }
  expect(writes()).toHaveLength(0)
})

it('confirms deletion at the owning model row, cancels without writing, and removes only that selection', async () => {
  await loaded()
  const remove = () => modelActions().find(node => node.props['aria-label'] === t('ui.providers.removeModelNamed', {name: 'Model 原文'}))!.props.onClick()
  remove()
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    expect(renderToStaticMarkup(feedback())).toContain(t('ui.providers.removeModelConfirm', {name: 'Model 原文'}))
    expect(button(feedback(), 'ui.providers.confirmRemoveModel').props.children).toBe(locale === 'zh-CN' ? '移除模型' : 'Remove model')
    expect(nodes(feedback()).filter(node => node.type === Button).map(node => node.props.children)).toEqual([t('ui.providers.cancel'), t('ui.providers.confirmRemoveModel')])
    expect(renderToStaticMarkup(feedback())).not.toContain(t('ui.providers.confirmRemove'))
    expect(feedback('b')).toBeNull()
    expect(feedback('a', 'one')).toBeNull()
    expect(feedback('a', 'two', 'first')).toBeNull()
  }
  button(feedback(), 'ui.providers.cancel').props.onClick()
  expect(feedback()).toBeNull(); expect(writes()).toHaveLength(0)
  remove(); await button(feedback(), 'ui.providers.confirmRemoveModel').props.onClick()
  expect(writes()).toHaveLength(1)
  expect(routing.providers[1].connections[1].models.map(model => model.id)).toEqual(['first'])
  expect(routing.providers[1].connections[0].models).toHaveLength(2)
  expect(routing.providers[0].connections[1].models).toHaveLength(2)
  expect(feedback('a', 'two', 'first')).toBeNull()
})

it.each(['success', 'failure'])('keeps provider removal confirmation by its header actions, with cancel first and the scoped %s result', async outcome => {
  await loaded()
  modelActions().find(node => node.props['aria-label'] === t('ui.providers.removeModelNamed', {name: 'Model 原文'}))!.props.onClick()
  expect(feedback()).not.toBeNull()
  const remove = () => nodes(providerCard()).find(node => node.props['aria-label'] === t('ui.providers.removeNamed', {name: 'Custom 原文'}))!.props.onClick()
  remove()
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const card = providerCard(), all = nodes(card)
    const confirmation = all.find(node => node.type === RemovalConfirmation)!
    expect(nodes(card.props.children[0])).toContain(confirmation)
    expect(all.indexOf(confirmation)).toBeLessThan(all.findIndex(node => node.type === ProviderModelPreferences))
    expect(renderToStaticMarkup(providerConfirmation())).toContain(t('ui.providers.removeConfirm', {name: 'Custom 原文'}))
    expect(nodes(providerConfirmation()).filter(node => node.type === Button).map(node => node.props.children)).toEqual([t('ui.providers.cancel'), t('ui.providers.confirmRemove')])
    expect(providerConfirmation('b')).toBeNull()
    expect(feedback()).toBeNull()
  }
  button(providerConfirmation(), 'ui.providers.cancel').props.onClick()
  expect(providerConfirmation()).toBeNull()
  expect(writes()).toHaveLength(0)
  remove()
  const write = deferred<{routing: ProviderSnapshot}>()
  fixture.request.mockImplementationOnce(() => write.promise)
  const confirm = button(providerConfirmation(), 'ui.providers.confirmRemove')
  const pending = confirm.props.onClick()
  await confirm.props.onClick()
  expect(writes()).toHaveLength(1)
  expect(writes()[0][2].revision).toBe('original')
  expect(writes()[0][2].providers.map((row: ModelProvider) => row.id)).toEqual(['b'])
  expect(button(providerConfirmation(), 'ui.providers.confirmRemove').props.disabled).toBe(true)
  expect(button(providerConfirmation(), 'ui.providers.cancel').props.disabled).toBe(true)
  if (outcome === 'success') {
    routing = {...routing, revision: 'removed', providers: writes()[0][2].providers}
    write.resolve({routing})
  } else write.reject(new ResourceError({key: 'ui.http.invalidResponse'}))
  await pending
  if (outcome === 'success') {
    expect(providerCard()).toBeUndefined()
    expect(providerCard('b')).toBeDefined()
  } else {
    for (const locale of ['en', 'zh-CN', 'en'] as const) {
      await setLocale(locale)
      expect(providerConfirmation()).not.toBeNull()
      expect(renderToStaticMarkup(render())).toContain(t('ui.http.invalidResponse'))
    }
    expect(routing.providers).toHaveLength(2)
  }
  expect(writes()).toHaveLength(1)
})

it.each([
  [{model: ''}, 'ui.providers.invalidModelId'],
  [{model: 'https://example.test/v1'}, 'ui.providers.invalidModelId'],
  [{model: 'remote-first'}, 'ui.providers.duplicateModel'],
  [{context_window: 0}, 'ui.providers.invalidModelCapacity'],
  [{max_output_tokens: 1.5}, 'ui.providers.invalidModelCapacity'],
] as const)('keeps invalid model metadata local: %j', async (change, key) => {
  await loaded(); editModel(); fields().props.onChange({...fields().props.value, ...change})
  const tree = render()
  expect(button(tree, 'ui.providers.save').props.disabled).toBe(true)
  expect(renderToStaticMarkup(tree)).toContain(t(key))
  await button(tree, 'ui.providers.save').props.onClick()
  expect(writes()).toHaveLength(0)
})

it.each(['resource', 'external'])('keeps %s save failures beside the top controls and retains the draft without replay on language changes', async kind => {
  await loaded(); editModel(); fields().props.onChange({...fields().props.value, name: 'Retry 原文'})
  fixture.request.mockRejectedValueOnce(kind === 'resource' ? new ResourceError({key: 'ui.http.invalidResponse'}) : new Error('External diagnostic 原文'))
  await button(render(), 'ui.providers.save').props.onClick()
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const tree = render(), header = nodes(tree).find(node => node.type === 'header')!
    expect(renderToStaticMarkup(header)).toContain(kind === 'resource' ? t('ui.http.invalidResponse') : 'External diagnostic 原文')
    expect(fields().props.value.name).toBe('Retry 原文')
  }
  expect(writes()).toHaveLength(1)
  expect(fixture.request.mock.calls.filter(call => call[0] === '/connection' && call[1] !== 'PATCH')).toHaveLength(2)
  expect(fixture.changed).not.toHaveBeenCalled()
})

it('does not publish a late model save into a closed page or a newly opened editor', async () => {
  await loaded(); editModel()
  const write = deferred<{routing: ProviderSnapshot}>(), obsolete = structuredClone(routing)
  obsolete.providers[1].name = 'Obsolete 原文'
  fixture.request.mockImplementationOnce(() => write.promise)
  const pending = button(render(), 'ui.providers.save').props.onClick()
  unmount(); hooks.slots = []; hooks.effects = []
  await loaded(); editModel(); fields().props.onChange({...fields().props.value, name: 'New draft 原文'})
  write.resolve({routing: obsolete}); await pending
  expect(fields().props.value.name).toBe('New draft 原文')
  expect(renderToStaticMarkup(render())).not.toContain('Obsolete 原文')
  expect(writes()).toHaveLength(1)
})
