import {useEffect, useRef, useState, type ReactNode} from 'react'
import {DefaultModelFeedback, ModelPreferences} from './ModelPreferences'
import {Plus, Trash2, ChevronUp, ChevronDown, ListPlus, RefreshCw, Pencil} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {ModelCatalog, ModelInfo, ResourceText} from '@/types'
import {runtimeRequest} from '@/lib/http'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import {connectionChanged} from '@/lib/connectionRoutes'
import {useAppStore} from '@/store/app'
import {Button} from '@/components/ui/primitives'
import {SecretInput} from '@/components/ui/SecretInput'
import {SaveStatus} from '@/components/ui/SaveStatus'
import {usePageVisible} from '@/hooks/usePageVisible'
import {CodexGatewayAccounts} from './CodexGatewayAccounts'
import {Switch} from './ExtensionCatalogControls'
import type {ProviderChoice} from './ProviderPickers'
import {ModelPagination, ModelPicker, ProviderPicker} from './ProviderPickers'
import {ManualModelEditor, ProviderModelFields} from './ProviderModelEditor'
import type {CatalogModel, ModelProvider, ProviderConnection, ProviderModel, ProviderSnapshot, ProviderTemplate} from '@/lib/providerConnections'
import {providerDraftForTemplate, providerModelGroups, providerCatalogChanges, reconcileProviderModels, providerConnectionIssue, sortProviderChoices, providerConfigurationState, validProviderEndpoint, validProviderModelId, maskProviderKey, moveProviderItem, newProviderConnection, newProviderKey, providerPayload} from '@/lib/providerConnections'

export function KiloFreeNotice() {
  useLocale()
  return <p className="text-xs text-warn">{t('ui.providers.kiloFreeHint')}</p>
}

const field = 'w-full min-w-0 rounded-lg border border-line bg-canvas px-3 py-2 text-xs'
type ModelTarget = {providerId: string; connectionId: string; modelId: string}

export function ModelConnections() {
  useLocale()
  const visible = usePageVisible()
  const models = useAppStore(state => state.models)
  const [snapshot, setSnapshot] = useState<ProviderSnapshot | null>(null)
  const [templates, setTemplates] = useState<ProviderTemplate[]>([])
  const [draft, setDraft] = useState<ModelProvider | null>(null)
  const [editingModel, setEditingModel] = useState<ModelTarget | null>(null)
  const [removingModel, setRemovingModel] = useState<ModelTarget | null>(null)
  const [adding, setAdding] = useState(false)
  const [subscription, setSubscription] = useState(false)
  const [busy, setBusy] = useState(false)
  const [probeCount, setProbeCount] = useState(0)
  const activeProbes = useRef(0)
  const [error, setError] = useState<ResourceText>('')
  const [saved, setSaved] = useState(false)
  const [credentialIssue, setCredentialIssue] = useState<{connection: string; key: string} | null>(null)
  const [removing, setRemoving] = useState<string | null>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const owner = useRef(0)
  const lock = useRef(false)
  const editor = useRef<HTMLFormElement>(null)
  const root = useRef<HTMLElement>(null)
  const modelElements = useRef(new Map<string, HTMLDivElement>())
  const returnTarget = useRef<ModelTarget | {providerId: string} | null>(null)
  useEffect(() => {
    if (!visible) return
    if (draft) {
      editor.current?.scrollIntoView({block: 'start'})
      editor.current?.querySelector<HTMLInputElement>('input')?.focus({preventScroll: true})
    } else if (returnTarget.current) {
      const target = returnTarget.current
      const provider = Array.from(root.current?.querySelectorAll<HTMLElement>('[data-provider-id]') ?? []).find(row => row.dataset.providerId === target.providerId)
      const row = 'modelId' in target ? modelElements.current.get(`harness/${target.providerId}/${target.connectionId}/${target.modelId}`) : provider
      row?.scrollIntoView({block: 'nearest'})
      row?.querySelector<HTMLButtonElement>('[data-edit-action]')?.focus({preventScroll: true})
      returnTarget.current = null
    }
  }, [draft?.id, editingModel?.connectionId, editingModel?.modelId, visible])
  useEffect(() => {
    if (!visible) return
    const generation = ++owner.current
    const controller = new AbortController()
    void Promise.all([
      runtimeRequest<ProviderSnapshot>('/connection', 'GET', undefined, {signal: controller.signal}),
      runtimeRequest<{providers: ProviderTemplate[]}>('/connection/providers', 'GET', undefined, {signal: controller.signal}),
      runtimeRequest<{accounts: Array<{logged_in: boolean; pending: boolean}>}>('/codex-gateway', 'GET', undefined, {signal: controller.signal}),
      runtimeRequest<ModelCatalog>('/models', 'GET', undefined, {signal: controller.signal}).then(data => ({data}), failure => ({error: failureText(failure)})),
    ]).then(([next, catalog, accounts, modelCatalog]) => {
      if (owner.current !== generation) return
      setSnapshot(next); setTemplates(catalog.providers); setSubscription(accounts.accounts.some(account => account.logged_in || account.pending))
      if ('data' in modelCatalog) useAppStore.setState({models: modelCatalog.data.models, defaultModel: modelCatalog.data.active})
      else setError({template: {key: 'ui.providers.catalogUnavailable', params: {reason: modelCatalog.error}}})
    }).catch(failure => {if (!controller.signal.aborted && owner.current === generation) setError(failureText(failure))})
    return () => {++owner.current; controller.abort()}
  }, [visible, loadAttempt])
  const persist = async (providers: ModelProvider[]) => {
    if (lock.current || activeProbes.current || !snapshot) return
    if (editingModel && modelIssue) return
    const invalid = draft?.connections.find(connection => providerConnectionIssue(connection))
    if (invalid) {
      setCredentialIssue({connection: invalid.id, key: providerConnectionIssue(invalid)!})
      if (editingModel) setError({key: providerConnectionIssue(invalid)!})
      const pane = editor.current?.querySelector<HTMLDetailsElement>(`details[data-connection-id="${invalid.id}"]`)
      if (pane) {pane.open = true; pane.scrollIntoView({block: 'nearest'})}
      return
    }
    if (draft && !editor.current?.reportValidity()) return
    lock.current = true; setBusy(true); setError(''); setSaved(false)
    const generation = owner.current
    try {
      const result = await runtimeRequest<{routing: ProviderSnapshot}>('/connection', 'PATCH', {providers: providerPayload(providers), revision: snapshot.revision})
      connectionChanged()
      if (owner.current === generation) {setSnapshot(result.routing); setDraft(null); setEditingModel(null); setRemoving(null); setRemovingModel(null); setCredentialIssue(null); setSaved(true)}
      try {
        const models = await runtimeRequest<{models: import('@/types').ModelInfo[]; active: string}>('/models')
        if (owner.current === generation) useAppStore.setState({models: models.models, defaultModel: models.active})
      } catch (failure) {if (owner.current === generation) setError({template: {key: 'ui.providers.savedCatalogUnavailable', params: {reason: failureText(failure)}}})}
    } catch (failure) {
      if (owner.current === generation) setError(failureText(failure))
      try {const next = await runtimeRequest<ProviderSnapshot>('/connection'); if (owner.current === generation) setSnapshot(next)} catch { /* 原保存失败保留，回查失败不能宣称已保存。 */ }
    } finally {lock.current = false; setBusy(false)}
  }
  const choices = sortProviderChoices<ProviderChoice>([{id: 'codex-account-pool', name: t('ui.codexGateway.accountsTitle'), keywords: 'OpenAI Codex ChatGPT'}, ...templates.filter(row => row.id !== 'openai-codex').map(row => ({id: row.id, name: row.name, keywords: ''}))])
  const configuredProviders = snapshot?.providers ?? []
  const template = templates.find(row => row.id === draft?.type)
  const modelConnection = draft?.connections.find(connection => connection.id === editingModel?.connectionId)
  const modelDraft = modelConnection?.models.find(model => model.id === editingModel?.modelId)
  const modelIssue = !modelDraft ? null : !validProviderModelId(modelDraft.model) ? 'ui.providers.invalidModelId'
    : modelConnection?.models.some(model => model.id !== modelDraft.id && model.model === modelDraft.model.trim()) ? 'ui.providers.duplicateModel'
    : [modelDraft.context_window, modelDraft.max_output_tokens].some(value => value !== undefined && (!Number.isSafeInteger(value) || value < 1)) ? 'ui.providers.invalidModelCapacity' : null
  const edit = (provider: ModelProvider, target: ModelTarget | null = null) => {
    if (lock.current || draft || !visible) return
    returnTarget.current = target ?? {providerId: provider.id}
    setEditingModel(target); setDraft(structuredClone(provider)); setRemoving(null); setRemovingModel(null); setSaved(false); setError('')
  }
  const cancel = () => {if (lock.current) return; setDraft(null); setEditingModel(null); setError(''); setCredentialIssue(null)}
  const saveDraft = () => {
    if (!draft) return
    const next = modelDraft ? {...draft, connections: draft.connections.map(connection => connection.id === editingModel?.connectionId ? {...connection, models: connection.models.map(model => model.id === modelDraft.id ? {...model, model: model.model.trim(), name: model.name.trim() || model.model.trim()} : model)} : connection)} : draft
    return persist(configuredProviders.some(provider => provider.id === next.id) ? configuredProviders.map(provider => provider.id === next.id ? next : provider) : [...configuredProviders, next])
  }
  return <section ref={root} className="space-y-4" aria-label={t('ui.providers.title')}>
    {!draft ? <header className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="ui-section-title">{t('ui.providers.title')}</h3><p className="mt-1 text-xs text-muted">{t('ui.providers.description')}</p><p className="mt-1 text-xs text-muted">{t('ui.components.overlays.SettingsDialog.60044c2733c0')}</p></div><Button size="sm" disabled={busy || !snapshot} onClick={() => {setAdding(true); setSaved(false)}}><Plus className="h-4 w-4" />{t('ui.providers.add')}</Button></header> : null}
    {error && !draft ? <div className="space-y-2"><p role="alert" className="text-xs text-danger">{noticeText(error)}</p>{snapshot ? <Button size="sm" onClick={() => {setError(''); setLoadAttempt(value => value + 1)}}>{t('ui.providers.retryLoading')}</Button> : null}</div> : null}
    {saved ? <SaveStatus phase="saved" /> : null}
    {!snapshot ? error ? <Button size="sm" onClick={() => {setError(''); setLoadAttempt(value => value + 1)}}>{t('ui.providers.retryLoading')}</Button> : <p role="status" className="text-xs text-muted">{t('ui.providers.loading')}</p> : null}
    {adding ? <ProviderPicker choices={choices} onClose={() => setAdding(false)} onPick={id => {setAdding(false); setSaved(false); if (id === 'codex-account-pool') {setSubscription(true); return} const selected = templates.find(row => row.id === id); if (!selected) return; setEditingModel(null); setError(''); setDraft(providerDraftForTemplate(selected, configuredProviders))}} /> : null}
    <DefaultModelFeedback />
    {subscription && !draft ? <CodexGatewayAccounts /> : null}
    {snapshot && !snapshot.providers.length && !subscription && !draft && !adding ? <div className="rounded-xl border border-dashed border-line p-6 text-center text-xs text-muted">{t('ui.providers.empty')}</div> : null}
    {!draft ? sortProviderChoices(configuredProviders).map(provider => <section key={provider.id} data-provider-id={provider.id} aria-label={provider.name} className="overflow-hidden rounded-xl border border-line">
      <div className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="min-w-0 flex-1"><h4 className="ui-card-title break-words">{provider.name}</h4><p className="mt-1 text-xs text-muted">{t('ui.providers.summary', {state: t(`ui.providers.state.${providerConfigurationState(provider)}`), models: provider.connections.reduce((count, connection) => count + connection.models.length, 0)})}</p></div>
        <div className="flex gap-2"><Button size="sm" data-edit-action disabled={busy} onClick={() => edit(provider)}>{t('ui.providers.edit')}</Button><Button size="sm" variant="ghost" disabled={busy} aria-label={t('ui.providers.removeNamed', {name: provider.name})} onClick={() => {setRemoving(provider.id); setRemovingModel(null)}}><Trash2 className="h-4 w-4" /></Button></div>
        {removing === provider.id ? <div className="w-full"><RemovalConfirmation message={t('ui.providers.removeConfirm', {name: provider.name})} confirmLabel={t('ui.providers.confirmRemove')} busy={busy} onConfirm={() => persist(configuredProviders.filter(row => row.id !== provider.id))} onCancel={() => setRemoving(null)} /></div> : null}
      </div>
      <ProviderModelPreferences provider={provider} models={models} modelElements={modelElements.current}
        renderActions={(connection, model) => <>
          <Button size="xs" variant="ghost" data-edit-action disabled={busy} aria-label={t('ui.providers.editModelNamed', {name: model.name || model.model})} icon={<Pencil aria-hidden className="h-3.5 w-3.5" />} onClick={() => edit(provider, {providerId: provider.id, connectionId: connection.id, modelId: model.id})}>{t('ui.providers.edit')}</Button>
          <Button size="xs" variant="ghost" disabled={busy} aria-label={t('ui.providers.removeModelNamed', {name: model.name || model.model})} title={t('ui.providers.removeModelNamed', {name: model.name || model.model})} onClick={() => {setRemoving(null); setRemovingModel({providerId: provider.id, connectionId: connection.id, modelId: model.id})}}><Trash2 aria-hidden className="h-3.5 w-3.5" /></Button>
        </>}
        renderFeedback={(connection, model) => removingModel?.providerId === provider.id && removingModel.connectionId === connection.id && removingModel.modelId === model.id ? <RemovalConfirmation
          message={t('ui.providers.removeModelConfirm', {name: model.name || model.model})} confirmLabel={t('ui.providers.confirmRemoveModel')} busy={busy}
          onConfirm={() => persist(configuredProviders.map(row => row.id === provider.id ? {...row, connections: row.connections.map(item => item.id === connection.id ? {...item, models: item.models.filter(value => value.id !== model.id)} : item)} : row))}
          onCancel={() => setRemovingModel(null)}
        /> : null}
      />
    </section>) : null}
    {draft && (editingModel ? modelDraft : template) ? <form ref={editor} onInvalid={event => {let parent = event.target instanceof HTMLElement ? event.target.parentElement : null; while (parent) {if (parent instanceof HTMLDetailsElement) parent.open = true; parent = parent.parentElement}}} autoComplete="off" onSubmit={event => event.preventDefault()} className="rounded-xl border border-line bg-surface">
      <header className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-t-xl border-b border-line bg-surface p-4">
        <div className="min-w-0 flex-1"><h3 className="ui-section-title">{t(editingModel ? 'ui.providers.editModel' : 'ui.providers.editProvider')}</h3><p className="mt-1 break-words text-xs text-muted">{modelDraft ? t('ui.providers.modelEditContext', {provider: draft.name, model: modelDraft.name || modelDraft.model}) : draft.name}</p></div>
        <div className="flex flex-wrap items-center gap-2"><Button size="sm" disabled={busy} onClick={cancel}>{t('ui.providers.cancel')}</Button><Button size="sm" variant="primary" disabled={busy || probeCount > 0 || !!modelIssue || !draft.name.trim() || !draft.connections.length} loading={busy} onClick={saveDraft}>{t('ui.providers.save')}</Button>{busy ? <SaveStatus phase="saving" /> : null}</div>
        {error ? <p role="alert" tabIndex={0} className="max-h-24 w-full overflow-y-auto text-xs text-danger [overflow-wrap:anywhere]">{noticeText(error)}</p> : null}
      </header>
      <div className="space-y-4 p-4">
      {modelDraft ? <>
        <ProviderModelFields value={modelDraft} disabled={busy} onChange={value => setDraft(current => current ? {...current, connections: current.connections.map(connection => connection.id === editingModel?.connectionId ? {...connection, models: connection.models.map(model => model.id === value.id ? value : model)} : connection)} : current)} />
        {modelIssue ? <p role="alert" className="text-xs text-danger">{t(modelIssue)}</p> : null}
        <p className="text-xs text-muted">{t('ui.providers.metadataHint')}</p>
      </> : template ? <>
      <label className="block space-y-1 text-xs"><span>{t('ui.providers.supplierName')}</span><input className={field} value={draft.name} onChange={event => setDraft({...draft, name: event.target.value})} disabled={busy} /></label>
      {draft.connections.map((connection, index) => <ConnectionEditor key={connection.id} connection={connection} template={template} busy={busy} removable={draft.connections.length > 1} initiallyOpen={index === 0 || !snapshot?.providers.some(provider => provider.connections.some(row => row.id === connection.id))} stored={!!snapshot?.providers.some(provider => provider.connections.some(row => row.id === connection.id))} onProbeStateChange={pending => {activeProbes.current = Math.max(0, activeProbes.current + (pending ? 1 : -1)); setProbeCount(activeProbes.current)}} validationIssue={credentialIssue?.connection === connection.id ? credentialIssue.key : undefined} onValidation={key => setCredentialIssue({connection: connection.id, key})} onChange={next => {setDraft(current => current?.id === draft.id ? {...current, connections: current.connections.map(row => row.id === next.id ? next : row)} : current); if (credentialIssue?.connection === next.id && !providerConnectionIssue(next)) setCredentialIssue(null)}} onRemove={() => setDraft({...draft, connections: draft.connections.filter((_, position) => position !== index)})} providerId={draft.id} onRefresh={next => {if (next) setSnapshot(next)}} />)}
      {template.id !== 'kilo-free' ? <details><summary className="cursor-pointer text-xs text-muted">{t('ui.providers.additionalConnections')}</summary><div className="mt-3"><Button size="sm" disabled={busy} onClick={() => setDraft({...draft, connections: [...draft.connections, newProviderConnection(template)]})}>{t('ui.providers.addConnection')}</Button></div></details> : null}
      </> : null}
      </div>
    </form> : null}
    {!draft ? <ModelPreferences models={models.filter(model => !model.id.startsWith('harness/'))} /> : null}
  </section>
}

/** 确认区紧邻来源操作靠右排列，长名称与窄容器可自然换行。 */
export function RemovalConfirmation({message, confirmLabel, busy, onConfirm, onCancel}: {
  message: string
  confirmLabel: string
  busy: boolean
  onConfirm: () => void | Promise<void>
  onCancel: () => void
}) {
  useLocale()
  return <div role="group" aria-label={message} className="ml-auto flex w-fit max-w-full flex-col items-end gap-2 rounded-lg border border-line bg-surface p-3">
    <p className="max-w-sm text-right text-xs text-muted [overflow-wrap:anywhere]">{message}</p>
    <div className="flex max-w-full flex-wrap justify-end gap-2">
      <Button size="sm" disabled={busy} onClick={onCancel}>{t('ui.providers.cancel')}</Button>
      <Button size="sm" variant="danger" disabled={busy} loading={busy} onClick={onConfirm}>{confirmLabel}</Button>
    </div>
  </div>
}

/** 以稳定身份归属模型，同名提供商和接口不会合并。 */
export function ProviderModelPreferences({provider, models, renderActions, renderFeedback, modelElements}: {
  provider: ModelProvider
  models: ModelInfo[]
  renderActions?: (connection: ProviderConnection, model: ProviderModel) => ReactNode
  renderFeedback?: (connection: ProviderConnection, model: ProviderModel) => ReactNode
  modelElements?: Map<string, HTMLDivElement>
}) {
  const groups = providerModelGroups(provider, models)
  const available = new Set(models.map(model => model.id))
  if (!groups.length) return null
  return <div className="space-y-3 border-t border-line px-4 pb-4 pt-3">
    {groups.map(group => {
      const connection = provider.connections.find(row => row.id === group.id)!
      const source = new Map(connection.models.map(model => [`harness/${provider.id}/${connection.id}/${model.id}`, model]))
      return <section key={group.id} aria-label={provider.connections.length > 1 ? group.name : undefined} className="space-y-2">
      {provider.connections.length > 1 ? <h5 className="ui-group-title break-words">{group.name}</h5> : null}
      <ModelPreferences models={group.models} grouped={false} heading={false} modelElements={modelElements} modelIdentifier={model => source.get(model.id)?.model} isAvailable={model => available.has(model.id)} renderActions={renderActions ? model => renderActions(connection, source.get(model.id)!) : undefined} renderFeedback={renderFeedback ? model => renderFeedback(connection, source.get(model.id)!) : undefined} />
    </section>})}
  </div>
}

type ConnectionProps = {onProbeStateChange: (pending: boolean) => void; validationIssue?: string; onValidation: (key: string) => void; removable: boolean; initiallyOpen: boolean; connection: ProviderConnection; template: ProviderTemplate; busy: boolean; stored: boolean; providerId: string; onChange: (next: ProviderConnection) => void; onRemove: () => void; onRefresh: (snapshot: ProviderSnapshot | null) => void}
export function ConnectionEditor({onProbeStateChange, validationIssue, onValidation, removable, initiallyOpen, connection, template, busy, stored, providerId, onChange, onRemove, onRefresh}: ConnectionProps) {
  useLocale()
  const connectionRef = useRef(connection)
  connectionRef.current = connection
  const [picking, setPicking] = useState(false)
  const [selectedSearch, setSelectedSearch] = useState('')
  const [selectedPage, setSelectedPage] = useState(0)
  const available = connection.model_catalog ?? template.models
  const [error, setError] = useState<ResourceText>('')
  const [loading, setLoading] = useState(false)
  const [fallback, setFallback] = useState(false)
  const [catalogChanges, setCatalogChanges] = useState<{added: number; retired: number; removed: number} | null>(null)
  const generation = useRef(0)
  const pageVisible = usePageVisible()
  const [expanded, setExpanded] = useState(initiallyOpen)
  const active = pageVisible && expanded
  const probeController = useRef<AbortController | null>(null)
  const probing = useRef(false)
  const automaticProbeStarted = useRef(false)
  useEffect(() => {++generation.current; probeController.current?.abort(); setLoading(false); setFallback(false); setCatalogChanges(null); return () => {++generation.current; probeController.current?.abort()}}, [active, connection.base_url, connection.protocol, connection.auth, connection.keys, connection.headers])
  const change = (value: Partial<ProviderConnection>) => {
    const current = connectionRef.current
    const next = {...current, ...value, ...(('base_url' in value || 'protocol' in value || 'auth' in value && value.auth?.type !== current.auth.type) ? {model_catalog: undefined} : {})}
    connectionRef.current = next
    onChange(next)
  }
  const probe = async () => {
    if (probing.current || !active || !validProviderEndpoint(connection.base_url)) return
    const issue = providerConnectionIssue(connection, true)
    if (issue) {onValidation(issue); return}
    probing.current = true
    onProbeStateChange(true)
    const controller = new AbortController(); probeController.current = controller
    const owner = ++generation.current
    setLoading(true); setError(''); setCatalogChanges(null); setFallback(false)
    try {const result = await runtimeRequest<{models: CatalogModel[]; fallback: boolean}>('/connection/discover', 'POST', {provider: template.id, connection: providerPayload([{id: providerId, type: template.id, name: template.name, connections: [connection]}])[0].connections[0]}, {signal: controller.signal}); if (owner === generation.current) {setFallback(!!result.fallback); if (!result.fallback) {const current = connectionRef.current; const models = reconcileProviderModels(current.models, current.model_catalog ?? template.models, result.models); setCatalogChanges({...providerCatalogChanges(current.model_catalog ?? template.models, result.models), removed: current.models.length - models.length}); change({model_catalog: result.models, models})}}} catch (failure) {if (owner === generation.current) setError(failureText(failure))} finally {probing.current = false; onProbeStateChange(false); if (owner === generation.current) setLoading(false)}
  }
  useEffect(() => {
    let disposed = false
    // 等待当前挂载稳定，StrictMode 的试挂载不发起目录读取。
    queueMicrotask(() => {
      if (disposed || template.id !== 'kilo-free' || !active || busy || connection.model_catalog !== undefined || automaticProbeStarted.current) return
      automaticProbeStarted.current = true
      void probe()
    })
    return () => {disposed = true}
  }, [template.id, active, busy, stored, connection.model_catalog])
  const endpointInvalid = !validProviderEndpoint(connection.base_url)
  const selected = connection.models.map((model, index) => ({model, index})).filter(({model}) => `${model.model} ${model.name}`.toLowerCase().includes(selectedSearch.trim().toLowerCase()))
  const currentSelectedPage = Math.min(selectedPage, Math.max(0, Math.ceil(selected.length / 10) - 1))
  const resetSelection = (models: ProviderConnection['models']) => {setSelectedPage(0); change({models})}
  return <details data-connection-id={connection.id} open={expanded} onToggle={event => setExpanded(event.currentTarget.open)} className="relative rounded-lg border border-line bg-raised p-3">
    <summary className="cursor-pointer pr-10"><span className="ui-card-title [overflow-wrap:anywhere]">{connection.name}</span>{!expanded ? <span className="ml-2 text-xs text-muted">{t('ui.providers.modelCounts', {total: available.length, selected: connection.models.length})}</span> : null}</summary>
    {removable ? <Button size="xs" variant="ghost" className="absolute right-3 top-3" disabled={busy} aria-label={t('ui.providers.removeConnection')} onClick={onRemove}><Trash2 className="h-4 w-4" /></Button> : null}
    <div className="mt-3 space-y-3">
    {template.id === 'kilo-free' ? <p className="text-xs text-muted">{t('ui.providers.anonymous')}</p> : <>
    <label className="block space-y-1 text-xs"><span>{t('ui.providers.protocol')}</span><select className={field} value={connection.protocol} disabled={busy} onChange={event => change({protocol: event.target.value})}><option value="">{t('ui.providers.nativeProtocol')}</option><option value="openai-completions">OpenAI Chat Completions</option><option value="openai-responses">OpenAI Responses</option><option value="anthropic-messages">Anthropic Messages</option></select></label>
    <label className="block space-y-1 text-xs"><span>{t('ui.providers.endpoint')}</span><input className={field} value={connection.base_url} disabled={busy} required={template.id === 'custom' || !!connection.protocol} autoComplete="off" spellCheck={false} inputMode="url" aria-invalid={endpointInvalid} ref={element => element?.setCustomValidity(endpointInvalid ? t('ui.providers.invalidEndpoint') : '')} name={`provider-endpoint-${connection.id}`} placeholder={t('ui.providers.endpointAutomatic')} onChange={event => change({base_url: event.target.value})} /></label>
    {endpointInvalid ? <p role="alert" className="text-xs text-danger">{t('ui.providers.invalidEndpoint')}</p> : null}
    {!connection.base_url && !connection.protocol ? <p className="break-all text-xs text-muted">{t('ui.providers.endpointInherited')}{[...new Set(template.models.map(model => model.baseUrl).filter(Boolean))].map(url => <span className="block" key={url}>{url}</span>)}</p> : null}
    <label className="block space-y-1 text-xs"><span>{t('ui.providers.authentication')}</span><select className={field} value={connection.auth.type} disabled={busy} onChange={event => change({auth: {type: event.target.value, env: connection.auth.env}, keys: event.target.value === 'api_key' ? connection.keys.length ? connection.keys : [newProviderKey()] : []})}>
      {template.api_key ? <option value="api_key">{t('ui.providers.apiKey')}</option> : null}{template.oauth ? <option value="oauth">{t('ui.providers.oauth')}</option> : null}<option value="environment">{t('ui.providers.environment')}</option>{template.id === 'opencode-local' ? <option value="opencode_local">{t('ui.providers.localAuth')}</option> : null}
    </select></label>
    </>}
    {connection.auth.type === 'api_key' ? <div className="space-y-2"><p className="text-xs text-muted">{t('ui.providers.sharedKeys')}</p>{connection.keys.map((key, index) => <div key={key.id} className="flex flex-wrap items-center gap-2"><label className="min-w-0 flex-1 text-xs"><span>{t('ui.providers.keyNumber', {number: index + 1})}</span><ProviderKeyInput identity={key.id} value={key.value ?? ''} mask={key.mask ?? ''} disabled={busy} onChange={value => change({keys: connection.keys.map(row => row.id === key.id ? {...row, value} : row)})} /></label><Switch checked={key.enabled} label={t('ui.providers.keyEnabled')} disabled={busy} onChange={enabled => change({keys: connection.keys.map(row => row.id === key.id ? {...row, enabled} : row)})} /><Button size="xs" disabled={busy || index === 0} aria-label={t('ui.providers.moveUp')} onClick={() => change({keys: moveProviderItem(connection.keys, index, -1)})}><ChevronUp className="h-4 w-4" /></Button><Button size="xs" disabled={busy || index === connection.keys.length - 1} aria-label={t('ui.providers.moveDown')} onClick={() => change({keys: moveProviderItem(connection.keys, index, 1)})}><ChevronDown className="h-4 w-4" /></Button><Button size="xs" disabled={busy || connection.keys.length === 1} aria-label={t('ui.providers.removeKey')} onClick={() => change({keys: connection.keys.filter(row => row.id !== key.id)})}><Trash2 className="h-4 w-4" /></Button>{key.available === false ? <span className="text-xs text-warn">{t('ui.providers.unavailable')}</span> : null}{key.in_use ? <span className="text-xs text-brand">{t('ui.providers.inUse')}</span> : null}</div>)}<Button size="xs" disabled={busy} onClick={() => change({keys: [...connection.keys, newProviderKey()]})}>{t('ui.providers.addKey')}</Button>{stored ? <Button size="xs" disabled={busy} onClick={() => {void runtimeRequest<ProviderSnapshot>('/connection/reset', 'POST', {connection_id: connection.id}).then(onRefresh).catch(failure => setError(failureText(failure)))}}>{t('ui.providers.resetKeys')}</Button> : null}</div> : null}
    {(connection.auth.type === 'environment' || ['amazon-bedrock', 'google-vertex', 'azure', 'cloudflare-ai-gateway', 'cloudflare-workers-ai'].includes(template.id)) ? <div className="space-y-2">{(template.env ?? []).map(name => <label key={name} className="block text-xs">{name}{/KEY|TOKEN|SECRET/.test(name) ? <SecretInput className={field} autoComplete="off" value={connection.auth.env[name] ?? ''} disabled={busy} onChange={event => change({auth: {...connection.auth, env: {...connection.auth.env, [name]: event.target.value}}})} /> : <input className={field} autoComplete="off" value={connection.auth.env[name] ?? ''} disabled={busy} onChange={event => change({auth: {...connection.auth, env: {...connection.auth.env, [name]: event.target.value}}})} />}</label>)}</div> : null}
    {template.id === 'kilo-free' ? <><p className="text-xs text-muted">{t('ui.providers.kiloFreeModels')}</p><KiloFreeNotice /></> : null}
    {connection.auth.type === 'environment' ? <p className="text-xs text-muted">{t('ui.providers.environmentHint')}</p> : null}
    {connection.auth.type === 'opencode_local' ? <p className="text-xs text-muted">{t('ui.providers.localHint')}</p> : null}
    {connection.auth.type === 'oauth' ? <ProviderAuthorization providerId={providerId} connection={connection} stored={stored} active={active} onRefresh={onRefresh} providerType={template.id} onAuthorized={id => change({auth: {...connection.auth, configured: true, authorization_id: id}})} /> : null}
    <details><summary className="cursor-pointer text-xs text-muted">{t('ui.providers.advanced')}</summary><div className="mt-3 space-y-3"><Switch checked={connection.enabled} label={t('ui.providers.connectionEnabled')} disabled={busy} onChange={enabled => change({enabled})} /><JsonField label={t('ui.providers.headers')} value={connection.headers ?? {}} disabled={busy} onChange={value => change({headers: value as Record<string, string>})} /><JsonField label={t('ui.providers.compat')} value={connection.compat ?? {}} disabled={busy} onChange={value => change({compat: value as Record<string, unknown>})} />{(['timeout_ms', 'max_retries'] as const).map(name => <label key={name} className="block text-xs">{t(`ui.providers.${name}`)}<input className={field} type="number" min={name === 'max_retries' ? '0' : '1'} value={connection[name] ?? (name === 'max_retries' ? 2 : 300000)} disabled={busy} onChange={event => change({[name]: Number(event.target.value)})} /></label>)}</div></details>
    <section className="space-y-4 border-t border-line pt-4" aria-label={t('ui.providers.models')}>
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1"><h5 className="ui-card-title">{t('ui.providers.models')}</h5><span className="text-xs text-muted">{t('ui.providers.modelCounts', {total: available.length, selected: connection.models.length})}</span></div>
        <div className="flex flex-wrap items-center gap-2">
          {available.length ? <Button size="sm" disabled={busy || loading} icon={<ListPlus aria-hidden className="h-4 w-4" />} onClick={() => setPicking(true)}>{t('ui.providers.chooseModels', {count: available.length})}</Button> : null}
          <Button size="sm" variant="ghost" disabled={busy || loading || endpointInvalid} icon={<RefreshCw aria-hidden className={loading ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />} onClick={() => void probe()}>{t(loading ? 'ui.providers.probing' : 'ui.providers.discover')}</Button>
        </div>
      </header>
      {validationIssue || error ? <p role="alert" className="text-xs text-danger">{noticeText(validationIssue ? {key: validationIssue} : error)}</p> : null}
      {catalogChanges !== null ? <p role="status" className="text-xs text-muted">{t('ui.providers.catalogUpdated', catalogChanges)}</p> : null}
      {fallback && !error ? <p role="status" className="text-xs text-warn">{t('ui.providers.discoverFallback')}</p> : null}
      {!available.length ? <p role="status" className="text-xs text-muted">{t('ui.providers.emptyCatalog')}</p> : null}
      {picking ? <ModelPicker context={connection.name} available={available} catalog={template.models} selected={connection.models} onClose={() => setPicking(false)} onConfirm={models => {setPicking(false); setSelectedSearch(''); resetSelection(models)}} /> : null}
      {template.id !== 'kilo-free' ? <ManualModelEditor models={connection.models} disabled={busy || loading} onAdd={model => {
        const models = connectionRef.current.models
        if (busy || loading || models.some(row => row.model === model.model)) return false
        change({models: [...models, model]})
        setSelectedSearch('')
        setSelectedPage(Math.floor(models.length / 10))
        return true
      }} /> : null}
      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><h5 className="ui-group-title">{t('ui.providers.selectedModels', {count: connection.models.length})}</h5>
          {connection.models.length > 10 || selectedSearch ? <input className={field + ' sm:w-64'} aria-label={t('ui.providers.searchSelectedModels')} placeholder={t('ui.providers.searchSelectedModels')} autoComplete="off" value={selectedSearch} onChange={event => {setSelectedSearch(event.target.value); setSelectedPage(0)}} /> : null}
        </div>
        <div role="group" aria-label={t('ui.providers.selectedModels', {count: connection.models.length})} className="space-y-2">
          {selected.slice(currentSelectedPage * 10, (currentSelectedPage + 1) * 10).map(({model, index}) => <div key={model.id} className="space-y-3 rounded-lg border border-line bg-surface p-3">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1 [overflow-wrap:anywhere]"><p className="text-compact font-medium text-ink">{model.name || model.model}</p>{model.name && model.name !== model.model ? <p className="mt-0.5 text-xs text-muted">{model.model}</p> : null}</div>
              <div className="flex shrink-0 items-center gap-1"><Button size="xs" variant="ghost" disabled={busy || index === 0} aria-label={t('ui.providers.moveUp')} onClick={() => change({models: moveProviderItem(connection.models, index, -1)})}><ChevronUp aria-hidden className="h-4 w-4" /></Button><Button size="xs" variant="ghost" disabled={busy || index === connection.models.length - 1} aria-label={t('ui.providers.moveDown')} onClick={() => change({models: moveProviderItem(connection.models, index, 1)})}><ChevronDown aria-hidden className="h-4 w-4" /></Button><Button size="xs" variant="ghost" disabled={busy} aria-label={t('ui.providers.removeModel')} onClick={() => change({models: connection.models.filter(row => row.id !== model.id)})}><Trash2 aria-hidden className="h-4 w-4" /></Button></div>
            </div>
            <details><summary className="cursor-pointer text-xs text-muted">{t('ui.providers.modelOptions')}</summary><div className="mt-3 space-y-3 border-t border-line pt-3"><ProviderModelFields value={model} disabled={busy} onChange={value => change({models: connection.models.map(row => row.id === model.id ? value : row)})} /><p className="text-xs text-muted">{t('ui.providers.metadataHint')}</p></div></details>
          </div>)}
        </div>
        {!connection.models.length ? <p className="text-xs text-muted">{t('ui.providers.selectionEmpty')}</p> : selectedSearch && !selected.length ? <p role="status" className="text-xs text-muted">{t('ui.providers.noSelectedModels')}</p> : null}
        <ModelPagination label={t('ui.providers.selectedModels', {count: connection.models.length})} total={selected.length} page={currentSelectedPage} size={10} onPage={setSelectedPage} />
      </section>
    </section>
  </div></details>
}

function ProviderKeyInput({identity, value, mask, disabled, onChange}: {identity: string; value: string; mask: string; disabled: boolean; onChange: (value: string) => void}) {
  const [editing, setEditing] = useState(false)
  const [revealed, setRevealed] = useState(false)
  const masked = maskProviderKey(value)
  return <SecretInput className={field} concealedType={editing ? 'password' : 'text'} revealed={revealed} onToggleReveal={() => setRevealed(!revealed)} showLabel={t('ui.providers.showKey')} hideLabel={t('ui.providers.hideKey')} autoComplete="new-password" name={`provider-credential-${identity}`} data-lpignore="true" data-1p-ignore="true" spellCheck={false} autoCapitalize="none" value={editing || revealed ? value : masked} placeholder={mask} required={!mask} disabled={disabled} toggleDisabled={!value} onFocus={() => setEditing(true)} onBlur={() => {setEditing(false); setRevealed(false)}} onChange={event => {if (editing || revealed) onChange(event.target.value)}} />
}

function JsonField({label, value, disabled, onChange}: {label: string; value: unknown; disabled: boolean; onChange: (value: unknown) => void}) {
  const [draft, setDraft] = useState(JSON.stringify(value, null, 2))
  const [invalid, setInvalid] = useState(false)
  return <label className="mt-2 block text-xs">{label}<textarea className={field + ' mt-1 font-mono'} value={draft} disabled={disabled} aria-invalid={invalid} onChange={event => {setDraft(event.target.value); try {onChange(JSON.parse(event.target.value)); setInvalid(false); event.currentTarget.setCustomValidity('')} catch {setInvalid(true); event.currentTarget.setCustomValidity(t('ui.providers.invalidJson'))}}} />{invalid ? <span role="alert" className="text-danger">{t('ui.providers.invalidJson')}</span> : null}</label>
}

type Authorization = {id: string; status: string; events: Array<{type: string; url?: string; verificationUri?: string; userCode?: string; message?: string}>; prompt: {type: string; message: string; options?: Array<{id: string; label: string}>} | null; error: string}
function ProviderAuthorization({providerId, providerType, connection, active, onRefresh, onAuthorized}: {providerId: string; providerType: string; onAuthorized: (id: string) => void; connection: ProviderConnection; stored: boolean; active: boolean; onRefresh: (snapshot: ProviderSnapshot | null) => void}) {
  const [state, setState] = useState<Authorization | null>(null)
  const [answer, setAnswer] = useState('')
  const [error, setError] = useState<ResourceText>('')
  const [busy, setBusy] = useState(false)
  const callbacks = useRef({onAuthorized, onRefresh})
  callbacks.current = {onAuthorized, onRefresh}
  const owner = useRef(0)
  const lock = useRef(false)
  useEffect(() => {++owner.current; return () => {++owner.current}}, [active])
  useEffect(() => {
    if (!active || !state || state.status !== 'pending') return
    const generation = owner.current
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const next = await runtimeRequest<Authorization>(`/connection/authorization/${state.id}`, 'GET', undefined, {signal: controller.signal})
        if (controller.signal.aborted || generation !== owner.current) return
        setState(next)
        if (next.status === 'completed') {callbacks.current.onAuthorized(next.id)}
        else if (next.status === 'pending') timer = setTimeout(poll, 1000)
      } catch (failure) {
        if (!controller.signal.aborted && generation === owner.current) {setError(failureText(failure)); timer = setTimeout(poll, 3000)}
      }
    }
    timer = setTimeout(poll, 1000)
    return () => {controller.abort(); clearTimeout(timer)}
  }, [active, state?.id, state?.status])
  const operate = async (path: string, method: 'POST' | 'DELETE', body?: unknown) => {
    if (lock.current || !active) return
    lock.current = true; setBusy(true); setError('')
    const generation = owner.current
    try {const next = await runtimeRequest<Authorization>(path, method, body); if (generation === owner.current) {setState(next); setAnswer('')}}
    catch (failure) {if (generation === owner.current) setError(failureText(failure))}
    finally {lock.current = false; setBusy(false)}
  }
  return <div className="space-y-2 text-xs"><p className="text-muted">{t(connection.auth.configured ? 'ui.providers.authorized' : 'ui.providers.loginHint')}</p><Button size="sm" disabled={busy || state?.status === 'pending'} onClick={() => void operate('/connection/authorization', 'POST', {provider_id: providerId, connection_id: connection.id, draft: {id: providerId, type: providerType, name: providerType, connections: [connection]}})}>{t('ui.providers.login')}</Button>{state?.status === 'pending' ? <Button size="sm" disabled={busy} onClick={() => void operate(`/connection/authorization/${state.id}`, 'DELETE')}>{t('ui.providers.cancel')}</Button> : null}{state?.events.map((event, index) => <p key={index}>{event.message}{event.url || event.verificationUri ? <a className="text-brand underline" href={event.url || event.verificationUri} target="_blank" rel="noreferrer">{t('ui.providers.openLogin')}</a> : null}{event.userCode ? <code className="ml-2">{event.userCode}</code> : null}</p>)}{state?.prompt ? <label className="block">{state.prompt.message}{state.prompt.options ? <select className={field} value={answer} disabled={busy} onChange={event => setAnswer(event.target.value)}><option value="" />{state.prompt.options.map(row => <option key={row.id} value={row.id}>{row.label}</option>)}</select> : state.prompt.type === 'secret' ? <SecretInput className={field} disabled={busy} value={answer} onChange={event => setAnswer(event.target.value)} /> : <input className={field} disabled={busy} value={answer} onChange={event => setAnswer(event.target.value)} />}<Button size="sm" disabled={busy || !answer} onClick={() => void operate(`/connection/authorization/${state.id}/answer`, 'POST', {value: answer})}>{t('ui.providers.continue')}</Button></label> : null}{state?.error || error ? <p role="alert" className="text-danger">{state?.error || noticeText(error)}</p> : null}</div>
}
