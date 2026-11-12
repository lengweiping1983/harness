import type {ModelInfo} from '@/types'
import {newIdentity} from '@/lib/ids'
export type ProviderModel = {id: string; model: string; name: string; enabled: boolean; context_window?: number; max_output_tokens?: number; input: string[]; reasoning_efforts?: Record<string, string | null> | false; compat?: Record<string, unknown>}
export type ProviderKey = {id: string; value?: string; mask?: string; enabled: boolean; available?: boolean; in_use?: boolean}
export type ProviderConnection = {id: string; name: string; protocol: string; base_url: string; enabled: boolean; auth: {type: string; env: Record<string, string>; configured?: boolean; authorization_id?: string}; keys: ProviderKey[]; models: ProviderModel[]; model_catalog?: CatalogModel[]; headers?: Record<string, string>; compat?: Record<string, unknown>; timeout_ms?: number; max_retries?: number}
export type ModelProvider = {id: string; type: string; name: string; connections: ProviderConnection[]}
export type ProviderSnapshot = {version: number; revision: string; providers: ModelProvider[]}
export type CatalogModel = {id: string; name: string; api?: string; baseUrl?: string; contextWindow?: number; maxTokens?: number; input?: string[]; reasoning?: boolean; thinkingLevelMap?: Record<string, string | null>; reasoningEfforts?: string[]; enabled?: boolean; supported?: boolean}
export type ProviderTemplate = {id: string; name: string; base_url: string; api_key: boolean; oauth: boolean; env: string[]; models: CatalogModel[]}
export const newProviderKey = (): ProviderKey => ({id: newIdentity(), value: '', enabled: true})
export const newProviderModel = (value: string, source?: CatalogModel): ProviderModel => ({id: newIdentity(), model: value, name: source?.name || value, enabled: true, input: source?.input || ['text'], ...(source?.contextWindow ? {context_window: source.contextWindow} : {}), ...(source?.maxTokens ? {max_output_tokens: source.maxTokens} : {}), ...(source?.thinkingLevelMap ? {reasoning_efforts: source.thinkingLevelMap} : source?.reasoningEfforts?.length ? {reasoning_efforts: Object.fromEntries(source.reasoningEfforts.map(level => [level, level === 'off' ? null : level]))} : {})})
export const maskProviderKey = (value: string) => value ? value.length > 4 ? '******' + value.slice(-4) : '******' : ''
export function newProviderConnection(template: ProviderTemplate): ProviderConnection {
  const authType = template.id === 'kilo-free' ? 'anonymous' : template.id === 'opencode-local' ? 'opencode_local' : ['amazon-bedrock', 'google-vertex'].includes(template.id) ? 'environment' : template.api_key ? 'api_key' : template.oauth ? 'oauth' : 'environment'
  return {id: newIdentity(), name: template.name, protocol: ['custom', 'opencode-zen'].includes(template.id) ? 'openai-completions' : '', base_url: template.base_url || '', enabled: true,
    auth: {type: authType, env: {}}, keys: authType === 'api_key' ? [newProviderKey()] : [], models: []}
}
/** 已添加的免费渠道直接编辑原配置，避免重复保存一份相同渠道。 */
export function providerDraftForTemplate(template: ProviderTemplate, providers: readonly ModelProvider[]): ModelProvider {
  const existing = template.id === 'kilo-free' ? providers.find(provider => provider.type === template.id) : undefined
  return existing ? structuredClone(existing) : {id: newIdentity(), type: template.id, name: template.name, connections: [newProviderConnection(template)]}
}
export function providerPayload(providers: ModelProvider[]) {
  return providers.map(provider => ({...provider, connections: provider.connections.map(connection => ({...connection,
    auth: {type: connection.auth.type, env: connection.auth.env, ...(connection.auth.authorization_id ? {authorization_id: connection.auth.authorization_id} : {})}, keys: connection.keys.map(key => ({id: key.id, enabled: key.enabled, ...(key.value?.trim() ? {value: key.value.trim()} : {})}))}))}))
}
export function moveProviderItem<T>(items: T[], index: number, delta: number): T[] {
  const next = [...items], target = index + delta
  if (target < 0 || target >= items.length) return items
  ;[next[index], next[target]] = [next[target], next[index]]
  return next
}
export function validProviderEndpoint(value: string): boolean {
  if (!value.trim()) return true
  try {const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !!url.hostname && !url.username && !url.password && !url.search && !url.hash} catch {return false}
}
export const validProviderModelId = (value: string) => !!value.trim() && !/^https?:\/\//i.test(value.trim())
export function providerConnectionIssue(connection: ProviderConnection, requireEnabledKey = false): string | null {
  if (connection.auth.type === 'api_key') {
    if (!connection.keys.length || connection.keys.some(key => !key.value?.trim() && !key.mask)) return 'ui.providers.keyRequired'
    if (requireEnabledKey && !connection.keys.some(key => key.enabled)) return 'ui.providers.enabledKeyRequired'
    const values = connection.keys.map(key => key.value?.trim()).filter(Boolean)
    if (new Set(values).size !== values.length) return 'ui.providers.duplicateKey'
  }
  if (connection.auth.type === 'oauth' && !connection.auth.configured) return 'ui.providers.authorizationRequired'
  return null
}
/** 内置目录模型继续由运行库提供档位；探测新增模型没有运行库来源，保存时保留其思考档位。 */
export function addProviderModels(current: ProviderModel[], available: CatalogModel[], catalog: readonly CatalogModel[] = []): ProviderModel[] {
  const known = new Set(current.map(model => model.model))
  const builtin = new Set(catalog.map(row => row.id))
  return [...current, ...available.filter(model => !known.has(model.id) && known.add(model.id)).map(model => newProviderModel(model.id, builtin.has(model.id) ? {...model, thinkingLevelMap: undefined, reasoningEfforts: undefined} : model))]
}
export function providerConfigurationState(provider: ModelProvider): 'disabled' | 'incomplete' | 'configured' {
  const enabled = provider.connections.filter(connection => connection.enabled)
  if (!enabled.length) return 'disabled'
  return enabled.every(connection => connection.models.some(model => model.enabled) && (connection.auth.type === 'api_key' ? connection.keys.some(key => key.enabled && (key.value || key.mask) && key.available !== false) : connection.auth.type === 'oauth' ? connection.auth.configured : true)) ? 'configured' : 'incomplete'
}

/** 只排列界面目录，保留配置、模型和凭据的实际顺序。 */
export function sortProviderChoices<T extends {id: string; name: string; type?: string}>(providers: readonly T[]): T[] {
  const collator = new Intl.Collator('en', {numeric: true, sensitivity: 'base'})
  const family = (provider: T) => {
    const id = provider.type ?? provider.id
    return id.startsWith('opencode') ? 'OpenCode' : id.startsWith('openai') || id === 'codex-account-pool' ? 'OpenAI' : provider.name
  }
  return [...providers].sort((a, b) => collator.compare(family(a), family(b)) || (Number(a.id === 'codex-account-pool') - Number(b.id === 'codex-account-pool')) || collator.compare(a.name, b.name) || collator.compare(a.id, b.id))
}

/** 显式批量添加时，各用途系列保留最新版本，日期快照不与无日期别名重复。 */
export function latestProviderModels(models: readonly CatalogModel[]): CatalogModel[] {
  const choices = new Map<string, {model: CatalogModel; version: number[]; date: number; alias: boolean}>()
  const compare = (a: number[], b: number[]) => {
    for (let index = 0; index < Math.max(a.length, b.length); index++) {
      const value = (a[index] ?? 0) - (b[index] ?? 0)
      if (value) return value
    }
    return 0
  }
  for (const model of models) {
    if (model.enabled === false || model.supported === false) continue
    const id = model.id.toLowerCase()
    const dated = id.match(/-(20\d{2}-\d{2}-\d{2}|20\d{6}|[01]\d[0-3]\d)$/)
    const date = dated ? Number(dated[1].replaceAll('-', '')) : 0
    const base = dated ? id.slice(0, -dated[0].length) : id
    const version = [...base.matchAll(/\d+(?:\.\d+)*/g)].filter(match => !/^[bmk](?:-|$)/.test(base.slice(match.index! + match[0].length))).flatMap(match => match[0].split('.').map(Number))
    const alias = /(?:^|[-:])latest$/.test(base)
    const family = base.replace(/(?:^|-)v(?=\d)/g, '-').replace(/\d+(?:\.\d+)*/g, (value, offset: number) => /^[bmk](?:-|$)/.test(base.slice(offset + value.length)) ? value : '')
      .replace(/(?:^|-)(?:latest|preview|experimental|exp)(?=-|$)/g, '').replace(/-+/g, '-').replace(/^-|-$/g, '')
    const candidate = {model, version, date, alias}
    const previous = choices.get(family)
    if (!previous || compare(version, previous.version) > 0 || compare(version, previous.version) === 0
      && (alias !== previous.alias ? alias : date === 0 && previous.date > 0 || date > 0 && previous.date > 0 && date > previous.date)) choices.set(family, candidate)
  }
  return [...choices.values()].map(choice => choice.model)
}

/** 获取成功后按新目录移除下架模型，保留仍存在模型的身份、排序及手工选项。 */
export function reconcileProviderModels(current: ProviderModel[], previous: readonly CatalogModel[], fresh: readonly CatalogModel[]): ProviderModel[] {
  const known = new Map(previous.map(model => [model.id, model]))
  const available = new Map(fresh.map(model => [model.id, model]))
  return current.filter(model => available.has(model.model)).map(model => {
    const old = known.get(model.model), next = available.get(model.model)!
    const result = {...model}
    if (model.name === old?.name || model.name === model.model) result.name = next.name || model.model
    for (const [field, source] of [['context_window', 'contextWindow'], ['max_output_tokens', 'maxTokens']] as const) {
      if (next[source] && (model[field] === undefined || model[field] === old?.[source])) result[field] = next[source]
    }
    if (next.input && JSON.stringify(model.input) === JSON.stringify(old?.input)) result.input = next.input
    const mapping = (source?: CatalogModel) => source?.thinkingLevelMap ?? (source?.reasoningEfforts?.length ? Object.fromEntries(source.reasoningEfforts.map(level => [level, level === 'off' ? null : level])) : undefined)
    if (model.reasoning_efforts && JSON.stringify(model.reasoning_efforts) === JSON.stringify(mapping(old))) {
      const updated = mapping(next)
      if (updated) result.reasoning_efforts = updated
      else delete result.reasoning_efforts
    }
    return result
  })
}

/** 按完整模型列表比较上下架，不把取消勾选或手工模型计入下架数量。 */
export function providerCatalogChanges(previous: readonly CatalogModel[], fresh: readonly CatalogModel[]) {
  const before = new Set(previous.map(model => model.id)), after = new Set(fresh.map(model => model.id))
  return {added: [...after].filter(id => !before.has(id)).length, retired: [...before].filter(id => !after.has(id)).length}
}

/** 按配置身份列出全部已保存模型，运行目录只补充元数据，不决定管理行是否存在。 */
export function providerModelGroups(provider: ModelProvider, models: ModelInfo[]) {
  const catalog = new Map(models.map(model => [model.id, model]))
  return provider.connections.map(connection => ({
    id: connection.id,
    name: connection.name,
    models: connection.models.map(model => {
      const id = `harness/${provider.id}/${connection.id}/${model.id}`
      return catalog.get(id) ?? {id, label: model.name || model.model, provider: provider.name,
        ...(model.context_window ? {context_window: model.context_window} : {}),
        ...(model.max_output_tokens ? {max_output: model.max_output_tokens} : {})}
    }),
  })).filter(group => group.models.length)
}
