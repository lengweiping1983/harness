import {modelDisplayName} from '../modelMenu'
import {describe, expect, it} from 'vitest'
import {providerDraftForTemplate, providerCatalogChanges, latestProviderModels, reconcileProviderModels, providerConnectionIssue, sortProviderChoices, addProviderModels, newProviderConnection, providerConfigurationState, validProviderEndpoint, validProviderModelId} from '../providerConnections'
import type {ModelProvider, ProviderTemplate} from '../providerConnections'

describe('provider configuration edits', () => {
  it('starts Kilo Free anonymously without preselecting Auto Free or any other model', () => {
    const template: ProviderTemplate = {id: 'kilo-free', name: 'Kilo Free', base_url: 'https://api.kilo.ai/api/gateway', api_key: false, oauth: false, env: [], models: [{id: 'kilo-auto/free', name: 'Auto Free'}]}
    const connection = newProviderConnection(template)
    expect(connection.auth).toEqual({type: 'anonymous', env: {}})
    expect(connection.keys).toEqual([])
    expect(connection.models).toEqual([])
    expect(providerConnectionIssue(connection, true)).toBeNull()
    expect(providerConfigurationState({id: 'free', type: template.id, name: template.name, connections: [connection]})).toBe('incomplete')
  })
  it('explains missing, duplicate and disabled draft credentials before discovery or saving', () => {
    const connection = newProviderConnection({id: 'openai', name: 'OpenAI', base_url: 'https://api.openai.com/v1', api_key: true, oauth: false, env: [], models: []})
    expect(providerConnectionIssue(connection)).toBe('ui.providers.keyRequired')
    connection.keys[0].mask = '******last'
    expect(providerConnectionIssue(connection)).toBeNull()
    connection.keys[0].enabled = false
    expect(providerConnectionIssue(connection)).toBeNull()
    expect(providerConnectionIssue(connection, true)).toBe('ui.providers.enabledKeyRequired')
    connection.keys = [{id: 'first', value: 'duplicate', enabled: true}, {id: 'second', value: ' duplicate ', enabled: true}]
    expect(providerConnectionIssue(connection)).toBe('ui.providers.duplicateKey')
    connection.auth.type = 'oauth'; connection.keys = []
    expect(providerConnectionIssue(connection)).toBe('ui.providers.authorizationRequired')
    connection.auth.configured = true
    expect(providerConnectionIssue(connection)).toBeNull()
    connection.auth = {type: 'environment', env: {}}
    expect(providerConnectionIssue(connection)).toBeNull()
  })
  it('groups OpenCode routes and places the Codex account pool with OpenAI without changing input order', () => {
    const entries = [{id: 'opencode-zen', name: 'OpenCode Zen'}, {id: 'openrouter', name: 'OpenRouter'}, {id: 'codex-account-pool', name: 'Codex 帐号池'}, {id: 'opencode-local', name: 'OpenCode Local'}, {id: 'deepseek', name: 'DeepSeek'}, {id: 'openai', name: 'OpenAI'}, {id: 'opencode-go', name: 'OpenCode Go'}]
    const original = entries.map(row => row.id)
    expect(sortProviderChoices(entries).map(row => row.id)).toEqual(['deepseek', 'openai', 'codex-account-pool', 'opencode-go', 'opencode-local', 'opencode-zen', 'openrouter'])
    expect(entries.map(row => row.id)).toEqual(original)
  })
  it('uses model names and never exposes an internal identity as a display fallback', () => {
    const id = 'harness/supplier/endpoint/model'
    expect(modelDisplayName(id, [{id, label: 'DeepSeek V4 Pro', provider: 'DeepSeek', supports_reasoning: false}], 'Unavailable')).toBe('DeepSeek V4 Pro')
    expect(modelDisplayName(id, [], 'Unavailable')).toBe('Unavailable')
    expect(modelDisplayName('remote-model', [], 'Unavailable')).toBe('remote-model')
  })
  it('adding discovered models preserves manually edited models, order and stable identities', () => {
    const current = [{id: 'stable', model: 'remote', name: 'Custom name', enabled: false, input: ['image'], context_window: 12345}]
    const next = addProviderModels(current, [{id: 'remote', name: 'Catalog name'}, {id: 'second', name: 'Second'}, {id: 'second', name: 'Duplicate'}])
    expect(next).toHaveLength(2)
    expect(next[0]).toBe(current[0])
    expect(next[1].model).toBe('second')
    expect(next[1].id).not.toBe('stable')
    expect(addProviderModels(next, [{id: 'second', name: 'Second'}])).toEqual(next)
  })
  it('keeps thinking levels for models added outside the built-in catalog only', () => {
    const catalog = [{id: 'deepseek-v4.1-flash', name: 'Flash'}]
    const levels = {off: null, low: 'low', high: 'high'}
    const added = addProviderModels([], [{id: 'deepseek-v4.1-flash-1017', name: 'Flash 1017', thinkingLevelMap: levels}], catalog)
    expect(added[0].reasoning_efforts).toEqual(levels)
    const builtin = addProviderModels([], [{id: 'deepseek-v4.1-flash', name: 'Flash', thinkingLevelMap: levels}], catalog)
    expect(builtin[0].reasoning_efforts).toBeUndefined()
    expect(addProviderModels([], [{id: 'manual-model', name: 'Manual'}], catalog)[0].reasoning_efforts).toBeUndefined()
  })
  it('environment and OAuth templates do not carry an empty API key', () => {
    const template: ProviderTemplate = {id: 'amazon-bedrock', name: 'AWS', base_url: '', api_key: true, oauth: false, env: [], models: []}
    const connection = newProviderConnection(template)
    expect(connection.auth.type).toBe('environment')
    expect(connection.keys).toEqual([])
    expect(connection.models).toEqual([])
    const api = newProviderConnection({...template, id: 'openai', base_url: 'https://api.openai.com/v1'})
    expect(api.base_url).toBe('https://api.openai.com/v1')
    expect(api.keys[0].value).toBe('')
  })
  it('starts new endpoints empty and explicitly added models retain metadata and independent identities', () => {
    const template: ProviderTemplate = {id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com', api_key: true, oauth: false, env: [], models: [{id: 'deepseek-chat', name: 'Chat', contextWindow: 128000}, {id: 'deepseek-reasoner', name: 'Reasoner', input: ['text', 'image']}]}
    const first = newProviderConnection(template), second = newProviderConnection(template)
    expect(first.models).toEqual([])
    expect(second.models).toEqual([])
    first.models = addProviderModels(first.models, template.models)
    second.models = addProviderModels(second.models, template.models)
    expect(first.models.map(model => model.model)).toEqual(['deepseek-chat', 'deepseek-reasoner'])
    expect(first.models.every(model => model.enabled)).toBe(true)
    expect(first.models[0].context_window).toBe(128000)
    expect(first.models[1].input).toEqual(['text', 'image'])
    expect(first.models[0].id).not.toBe(second.models[0].id)
  })
  it('flags endpoint mistakes and avoids accepting an endpoint as a manual model ID', () => {
    for (const value of ['', 'https://gateway.example/v1', 'http://127.0.0.1:9010/v1']) expect(validProviderEndpoint(value)).toBe(true)
    for (const value of ['example.com', 'ftp://example.com', 'https://user:secret@example.com', 'https://example.com?key=secret', 'https://example.com/#fragment']) expect(validProviderEndpoint(value)).toBe(false)
    expect(validProviderModelId('https://gateway.example/v1')).toBe(false)
    expect(validProviderModelId(' ')).toBe(false)
    expect(validProviderModelId('supplier/model:latest')).toBe(true)
  })
  it('configuration status never treats a failed or disabled key as ready', () => {
    const template: ProviderTemplate = {id: 'openai', name: 'OpenAI', base_url: 'https://api.openai.com/v1', api_key: true, oauth: false, env: [], models: []}
    const connection = newProviderConnection(template)
    const provider: ModelProvider = {id: 'supplier', type: template.id, name: 'Supplier', connections: [connection]}
    expect(providerConfigurationState(provider)).toBe('incomplete')
    connection.models = [{id: 'model', model: 'remote', name: 'Remote', enabled: true, input: ['text']}]
    connection.keys[0].mask = '******last'; connection.keys[0].available = false
    expect(providerConfigurationState(provider)).toBe('incomplete')
    connection.keys[0].available = true
    expect(providerConfigurationState(provider)).toBe('configured')
    connection.enabled = false
    expect(providerConfigurationState(provider)).toBe('disabled')
  })
})

it('defaults to the latest version per purpose series, removes dated duplicates and preserves all catalog options', () => {
  const models = ['deepseek-v4-flash-0731', 'deepseek-v4.1-flash', 'deepseek-v4-pro', 'deepseek-v4-pro-0813', 'glm-5.2', 'glm-5.3', 'qwen3.6-plus', 'qwen3.7-plus', 'qwen3.8-max'].map(id => ({id, name: id}))
  expect(latestProviderModels(models).map(model => model.id)).toEqual(['deepseek-v4.1-flash', 'deepseek-v4-pro', 'glm-5.3', 'qwen3.7-plus', 'qwen3.8-max'])
  expect(models).toHaveLength(9)
})
it('refresh removes retired selections and updates reported caps while retaining manual names, identity and order', () => {
  const current = [{id: 'stable-a', model: 'current', name: 'Manual name', context_window: 100, input: ['text'], enabled: false}, {id: 'retired-id', model: 'retired', name: 'Retired', input: ['text'], enabled: true}]
  const previous = [{id: 'current', name: 'Original name', contextWindow: 100}]
  const fresh = [{id: 'current', name: 'New name', contextWindow: 200}, {id: 'added', name: 'Added'}]
  expect(reconcileProviderModels(current, previous, fresh)).toEqual([{...current[0], context_window: 200}])
  expect(current).toHaveLength(2)
  expect(reconcileProviderModels(current, previous, [])).toEqual([])
})
it('reported reasoning levels on a newly discovered model are retained for saving', () => {
  const added = addProviderModels([], [{id: 'fresh', name: 'Fresh', reasoningEfforts: ['low', 'high', 'max']}])
  expect(added[0].reasoning_efforts).toEqual({low: 'low', high: 'high', max: 'max'})
})

it('keeps model sizes separate and never defaults to an explicitly unsupported release', () => {
  const models = [{id: 'gpt-oss-20b', name: 'Small'}, {id: 'gpt-oss-120b', name: 'Large'}, {id: 'glm-5', name: 'Available'}, {id: 'glm-6', name: 'Upgrade required', supported: false}]
  expect(latestProviderModels(models).map(model => model.id)).toEqual(['gpt-oss-20b', 'gpt-oss-120b', 'glm-5'])
})
it('refreshes declared reasoning mappings while preserving explicit manual mappings', () => {
  const previous = [{id: 'm', name: 'M', thinkingLevelMap: {high: 'high'}}]
  const fresh = [{id: 'm', name: 'M', thinkingLevelMap: {high: 'large', max: 'max'}}]
  const automatic = {id: 'local', model: 'm', name: 'M', input: ['text'], enabled: true, reasoning_efforts: {high: 'high'}}
  expect(reconcileProviderModels([automatic], previous, fresh)[0].reasoning_efforts).toEqual({high: 'large', max: 'max'})
  const manual = {...automatic, reasoning_efforts: {high: 'user-value'}}
  expect(reconcileProviderModels([manual], previous, fresh)[0].reasoning_efforts).toEqual({high: 'user-value'})
})

it('does not let an older versioned latest alias defeat a newer concrete release', () => {
  expect(latestProviderModels([{id: 'glm-5.2-latest', name: 'Old alias'}, {id: 'glm-5.3', name: 'New'}]).map(model => model.id)).toEqual(['glm-5.3'])
})

it('counts additions and retirements across the complete list independently of selection', () => {
  expect(providerCatalogChanges([{id: 'kept', name: 'K'}, {id: 'unselected-retired', name: 'R'}], [{id: 'kept', name: 'Renamed'}, {id: 'added', name: 'A'}])).toEqual({added: 1, retired: 1})
  expect(providerCatalogChanges([], [])).toEqual({added: 0, retired: 0})
  expect(providerCatalogChanges([], [{id: 'a', name: 'A'}])).toEqual({added: 1, retired: 0})
  expect(providerCatalogChanges([{id: 'a', name: 'A'}], [])).toEqual({added: 0, retired: 1})
})

it('accepts anonymous Kilo save and readback responses through the generated HTTP contract', async () => {
  const {responseIssue} = await import('../responseValidation')
  const connection = newProviderConnection({id: 'kilo-free', name: 'Kilo Free', base_url: 'https://api.kilo.ai/api/gateway', api_key: false, oauth: false, env: [], models: [{id: 'kilo-auto/free', name: 'Auto Free'}]})
  const routing = {version: 2, revision: 'saved', providers: [{id: 'kilo', type: 'kilo-free', name: 'Kilo Free', connections: [connection]}], models: [], current: null, cycle: 1}
  expect(responseIssue('/api/v1/connection', 'GET', 200, routing)).toBeUndefined()
  expect(responseIssue('/api/v1/connection', 'PATCH', 200, {routing, config: {}})).toBeUndefined()
  expect(responseIssue('/api/v1/connection', 'GET', 200, {...routing, providers: [{...routing.providers[0], connections: [{...connection, auth: {type: 'unknown', env: {}}}]}]})).toBeDefined()
})

it('fetching a free channel leaves initial selection empty and preserves subsequent explicit choices', () => {
  const template: ProviderTemplate = {id: 'kilo-free', name: 'Kilo Free', base_url: 'https://api.kilo.ai/api/gateway', api_key: false, oauth: false, env: [], models: [{id: 'kilo-auto/free', name: 'Auto Free'}]}
  const connection = newProviderConnection(template)
  const fresh = [{id: 'kilo-auto/free', name: 'Auto Free', contextWindow: 64000}, {id: 'vendor/coder:free', name: 'Coder', thinkingLevelMap: {high: 'high'}, contextWindow: 128000}]
  expect(reconcileProviderModels(connection.models, template.models, fresh)).toEqual([])
  const explicit = addProviderModels([], [fresh[1]])
  expect(explicit[0].reasoning_efforts).toEqual({high: 'high'})
  const selected = {...connection, models: explicit}
  const refreshed = reconcileProviderModels(selected.models, fresh, [...fresh, {id: 'new:free', name: 'New'}])
  expect(refreshed.map(model => model.model)).toEqual(['vendor/coder:free'])
  expect(refreshed[0].id).toBe(explicit[0].id)
  expect(reconcileProviderModels(selected.models, fresh, [])).toEqual([])
  expect(connection.models).toEqual([])
})

it('reuses the configured Kilo channel when picked again and isolates the editing draft', () => {
  const template: ProviderTemplate = {id: 'kilo-free', name: 'Kilo Free', base_url: 'https://api.kilo.ai/api/gateway', api_key: false, oauth: false, env: [], models: [{id: 'kilo-auto/free', name: 'Auto Free'}]}
  const first = providerDraftForTemplate(template, [])
  expect(first.connections[0].models).toEqual([])
  first.connections[0].models = addProviderModels([], template.models)
  const picked = providerDraftForTemplate(template, [first])
  expect(picked).toEqual(first)
  expect(picked.id).toBe(first.id)
  picked.connections[0].models[0].name = 'Edited'
  expect(first.connections[0].models[0].name).toBe('Auto Free')
  const again = providerDraftForTemplate(template, [first])
  expect(again.connections[0].models[0].id).toBe(first.connections[0].models[0].id)
  const anotherTemplate = {...template, id: 'custom'}
  expect(providerDraftForTemplate(anotherTemplate, [{...first, type: 'custom'}]).id).not.toBe(first.id)
})
