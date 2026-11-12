import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {setLocale, t} from '@/i18n'
import type {ModelInfo} from '@/types'
import {modelSaveKey, useAppStore} from '@/store/app'
type AppState = ReturnType<typeof useAppStore.getState>
import type {ModelProvider} from '../providerConnections'
import {addProviderModels, newProviderConnection, providerModelGroups} from '../providerConnections'
import {ConnectionEditor, KiloFreeNotice, ProviderModelPreferences} from '@/components/overlays/ModelConnections'
import {DefaultModelFeedback} from '@/components/overlays/ModelPreferences'

const fixture = vi.hoisted(() => ({state: {} as AppState}))
vi.mock('@/store/app', async importOriginal => {
  const actual = await importOriginal<typeof import('@/store/app')>()
  return {...actual, useAppStore: (selector: (state: AppState) => unknown) => selector(fixture.state)}
})

const provider: ModelProvider = {
  id: 'one', type: 'opencode-local', name: 'OpenCode Local', connections: [{
    id: 'endpoint', name: 'OpenCode Local', protocol: '', base_url: '', enabled: true,
    auth: {type: 'environment', env: {}}, keys: [],
    models: [{id: 'flash', model: 'remote-flash', name: 'Flash 原文', enabled: true, input: ['text']}],
  }],
}
const models: ModelInfo[] = [
  {id: 'harness/two/endpoint/flash', label: 'Other provider', provider: 'OpenCode Local / OpenCode Local'},
  {id: 'harness/one/endpoint/flash', label: 'Flash 原文', provider: 'OpenCode Local / OpenCode Local', context_window: 12000},
  {id: 'harness/one/second/flash', label: 'Second connection', provider: 'OpenCode Local / OpenCode Local'},
]

beforeEach(() => {
  fixture.state = {
    models, defaultModel: models[1].id, modelVisibility: {}, modelVisibilitySaves: {}, modelSaves: {},
    defaultModelSave: null, setDefaultModel: vi.fn(), setModelVisibility: vi.fn(), dismissDefaultModelFeedback: vi.fn(),
  } as unknown as AppState
})
afterEach(() => setLocale('zh-CN'))

it('keeps identically named providers and endpoints separate by their full stable identities', () => {
  const multiple = {...provider, connections: [provider.connections[0], {...provider.connections[0], id: 'second'}]}
  expect(providerModelGroups(multiple, models).map(group => group.models.map(model => model.id))).toEqual([
    ['harness/one/endpoint/flash'], ['harness/one/second/flash'],
  ])
  expect(providerModelGroups(provider, [models[0]])[0].models).toEqual([{id: models[1].id, label: 'Flash 原文', provider: provider.name}])
  const unavailable = {...provider, connections: [{...provider.connections[0], models: [...provider.connections[0].models, {...provider.connections[0].models[0], id: 'missing'}]}]}
  expect(providerModelGroups(unavailable, models)[0].models.map(model => model.id)).toEqual([models[1].id, 'harness/one/endpoint/missing'])
})

it('keeps saved model rows and their actions when the runtime catalog is empty, without inventing selectable models', () => {
  const configured = {...provider, connections: [{...provider.connections[0], models: [{...provider.connections[0].models[0], context_window: 64000}]}]}
  const before = JSON.stringify(configured)
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const actions = vi.fn((_connection, model) => <button>{t('ui.providers.editModelNamed', {name: model.name})}</button>)
    const html = renderToStaticMarkup(<ProviderModelPreferences provider={configured} models={[]} renderActions={actions} />)
    expect(html).toContain('Flash 原文')
    expect(html).toContain(t('ui.models.contextSize', {count: '64k'}))
    expect(actions).toHaveBeenCalledWith(configured.connections[0], configured.connections[0].models[0])
    expect(html.match(/<button[^>]*role="switch"[^>]*>/)?.[0]).toContain('disabled=""')
    expect(html.match(/<button[^>]*disabled=""[^>]*>/g)).toHaveLength(2)
    expect(html).not.toContain('harness/')
    expect(fixture.state.setDefaultModel).not.toHaveBeenCalled()
    expect(fixture.state.setModelVisibility).not.toHaveBeenCalled()
  }
  expect(JSON.stringify(configured)).toBe(before)
})

it('renders one provider model list without repeated provider headings or internal IDs through English, Chinese, English', () => {
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const html = renderToStaticMarkup(<ProviderModelPreferences provider={provider} models={models} />)
    expect(html).toContain('Flash 原文')
    expect(html).toContain(t('ui.models.contextSize', {count: '12k'}))
    expect(html).toContain(t('ui.components.overlays.SettingsDialog.ba336585f0a2'))
    expect(html).toContain('aria-checked="true"')
    expect(html).not.toContain('<h5')
    expect(html).not.toContain(' / ')
    expect(html).not.toContain('harness/')
    expect(html).not.toContain('Other provider')
    expect(html).not.toContain('Second connection')
    expect(fixture.state.setDefaultModel).not.toHaveBeenCalled()
    expect(fixture.state.setModelVisibility).not.toHaveBeenCalled()
  }
})

it('retains separate connection headings and model order within the provider', () => {
  const multiple = {...provider, connections: [{...provider.connections[0], name: 'First 接口'}, {...provider.connections[0], id: 'second', name: 'Second 接口'}]}
  const html = renderToStaticMarkup(<ProviderModelPreferences provider={multiple} models={models} />)
  expect(html.match(/<h5 /g)).toHaveLength(2)
  expect(html.indexOf('First 接口')).toBeLessThan(html.indexOf('Flash 原文'))
  expect(html.indexOf('Flash 原文')).toBeLessThan(html.indexOf('Second 接口'))
  expect(html.indexOf('Second 接口')).toBeLessThan(html.indexOf('Second connection'))
  expect(html).toContain(t('ui.models.contextUnknown'))
})

it('exposes the provider model ID and output capacity alongside its display name', () => {
  const configured = {...provider, connections: [{...provider.connections[0], models: [{...provider.connections[0].models[0], model: 'vendor/complete-model-identifier-2026'}]}]}
  const catalog = [{...models[1], max_output: 4000}]
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const html = renderToStaticMarkup(<ProviderModelPreferences provider={configured} models={catalog} />)
    expect(html).toContain('Flash 原文')
    expect(html).toContain('vendor/complete-model-identifier-2026')
    expect(html).toContain(t('ui.models.outputSize', {count: '4k'}))
    expect(html).not.toContain('harness/')
  }
})

it('delivers row actions and inline feedback to the full provider connection model identity through English Chinese English', () => {
  const multiple = {...provider, connections: [provider.connections[0], {...provider.connections[0], id: 'second'}]}
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const actions = vi.fn((connection, model) => <button aria-label={t('ui.providers.editModelNamed', {name: model.name})} data-owner={connection.id}>{t('ui.providers.edit')}</button>)
    const feedback = vi.fn((connection, model) => connection.id === 'second' ? <p role="status">{t('ui.providers.removeModelConfirm', {name: model.name})}</p> : null)
    const html = renderToStaticMarkup(<ProviderModelPreferences provider={multiple} models={models} renderActions={actions} renderFeedback={feedback} />)
    expect(actions.mock.calls.map(([connection, model]) => [connection.id, model.id])).toEqual([['endpoint', 'flash'], ['second', 'flash']])
    expect(html).toContain('data-owner="endpoint"')
    expect(html).toContain('data-owner="second"')
    expect(html).toContain(t('ui.providers.removeModelConfirm', {name: 'Flash 原文'}))
    expect(html.match(/role="status"/g)).toHaveLength(1)
    expect(html).not.toContain('Other provider')
    expect(html).not.toContain('harness/')
    expect(fixture.state.setDefaultModel).not.toHaveBeenCalled()
  }
})

it('keeps hidden switches and their pending or failed feedback in the owning model list', () => {
  const id = models[1].id
  fixture.state.modelVisibility = {[id]: false}
  fixture.state.modelVisibilitySaves = {[id]: {pending: true, target: true, error: null, unconfirmed: false}}
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const html = renderToStaticMarkup(<ProviderModelPreferences provider={provider} models={models} />)
    expect(html).toContain('aria-checked="false"')
    expect(html).toContain('aria-busy="true"')
    expect(html).toContain(t('ui.settings.models.showing', {model: 'Flash 原文'}))
  }
  fixture.state.modelVisibilitySaves[id] = {pending: false, target: true, error: {key: 'ui.http.invalidResponse'}, unconfirmed: true}
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const html = renderToStaticMarkup(<ProviderModelPreferences provider={provider} models={models} />)
    expect(html).toContain(t('ui.http.invalidResponse'))
    expect(html).toContain(t('ui.settings.models.retryShow', {model: 'Flash 原文'}))
  }
})

it('retains global default save feedback even while its provider is being edited or absent', () => {
  fixture.state.defaultModelSave = {target: models[1].id, error: null, unconfirmed: false}
  fixture.state.modelSaves = {[modelSaveKey(null)]: true}
  expect(renderToStaticMarkup(<DefaultModelFeedback />)).toContain(t('ui.settings.models.savingDefault', {model: 'Flash 原文'}))
  fixture.state.modelSaves = {}
  fixture.state.defaultModelSave.error = {key: 'ui.http.invalidResponse'}
  fixture.state.defaultModelSave.unconfirmed = true
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const html = renderToStaticMarkup(<DefaultModelFeedback />)
    expect(html).toContain(t('ui.http.invalidResponse'))
    expect(html).toContain(t('ui.settings.models.retryDefault', {model: 'Flash 原文'}))
  }
})

it('renders the anonymous free pool limits and data notice through English, Chinese, English', () => {
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const html = renderToStaticMarkup(<KiloFreeNotice />)
    expect(html).toContain(t('ui.providers.kiloFreeHint'))
    expect(html).toContain(language === 'en' ? 'no login or API key' : '无需登录或 API Key')
  }
})

it('renders Kilo as an anonymous channel with concrete selectable models and without endpoint or manual-add forms', () => {
  const template = {id: 'kilo-free', name: 'Kilo Free', base_url: 'https://api.kilo.ai/api/gateway', api_key: false, oauth: false, env: [], models: [{id: 'kilo-auto/free', name: 'Auto Free'}, {id: 'vendor/coder:free', name: 'Coder 原文'}]}
  const connection = newProviderConnection(template)
  connection.models = addProviderModels([], template.models)
  const change = vi.fn(), validation = vi.fn(), probe = vi.fn()
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const html = renderToStaticMarkup(<ConnectionEditor connection={{...connection, model_catalog: template.models}} template={template} busy={false} stored={true} providerId="free" initiallyOpen removable={false} onChange={change} onRemove={vi.fn()} onRefresh={vi.fn()} onValidation={validation} onProbeStateChange={probe} />)
    expect(html).toContain(t('ui.providers.anonymous'))
    expect(html).toContain(t('ui.providers.kiloFreeModels'))
    expect(html).toContain(t('ui.providers.chooseModels', {count: 2}))
    expect(html).toContain('Coder 原文')
    expect(html).toContain('vendor/coder:free')
    expect(html).not.toContain(t('ui.providers.manualConfiguration'))
    expect(html).not.toContain(t('ui.providers.authentication'))
    expect(html).not.toContain(t('ui.providers.endpoint'))
    expect(connection.models.map(model => model.model)).toEqual(['kilo-auto/free', 'vendor/coder:free'])
  }
  expect(change).not.toHaveBeenCalled()
  expect(validation).not.toHaveBeenCalled()
  expect(probe).not.toHaveBeenCalled()
})
