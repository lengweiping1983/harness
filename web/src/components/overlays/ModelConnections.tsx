import {newIdentity} from '@/lib/ids'
import {useEffect, useRef, useState} from 'react'
import {Plus, Eye, EyeOff, Trash2, ChevronUp, ChevronDown} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {ResourceText} from '@/types'
import {runtimeRequest} from '@/lib/http'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import {connectionChanged} from '@/lib/connectionRoutes'
import {useAppStore} from '@/store/app'
import {Button} from '@/components/ui/primitives'
import {SaveStatus} from '@/components/ui/SaveStatus'
import {usePageVisible} from '@/hooks/usePageVisible'
import {CodexGatewayAccounts} from './CodexGatewayAccounts'
import {Switch} from './ExtensionCatalogControls'
import type {ProviderChoice} from './ProviderPickers'
import {ModelPagination, ModelPicker, ProviderPicker} from './ProviderPickers'
import type {CatalogModel, ModelProvider, ProviderConnection, ProviderSnapshot, ProviderTemplate} from '@/lib/providerConnections'
import {providerCatalogChanges, reconcileProviderModels, providerConnectionIssue, sortProviderChoices, providerConfigurationState, validProviderEndpoint, validProviderModelId, maskProviderKey, moveProviderItem, newProviderConnection, newProviderKey, newProviderModel, providerPayload} from '@/lib/providerConnections'

const field = 'w-full min-w-0 rounded-lg border border-line bg-canvas px-3 py-2 text-xs'

export function ModelConnections() {
  useLocale()
  const visible = usePageVisible()
  const [snapshot, setSnapshot] = useState<ProviderSnapshot | null>(null)
  const [templates, setTemplates] = useState<ProviderTemplate[]>([])
  const [draft, setDraft] = useState<ModelProvider | null>(null)
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
  useEffect(() => {
    if (!visible) return
    const generation = ++owner.current
    const controller = new AbortController()
    void Promise.all([runtimeRequest<ProviderSnapshot>('/connection', 'GET', undefined, {signal: controller.signal}), runtimeRequest<{providers: ProviderTemplate[]}>('/connection/providers', 'GET', undefined, {signal: controller.signal}), runtimeRequest<{accounts: Array<{logged_in: boolean; pending: boolean}>}>('/codex-gateway', 'GET', undefined, {signal: controller.signal})]).then(([next, catalog, accounts]) => {
      if (owner.current === generation) {setSnapshot(next); setTemplates(catalog.providers); setSubscription(accounts.accounts.some(account => account.logged_in || account.pending))}
    }).catch(failure => {if (!controller.signal.aborted && owner.current === generation) setError(failureText(failure))})
    return () => {++owner.current; controller.abort()}
  }, [visible, loadAttempt])
  const persist = async (providers: ModelProvider[]) => {
    if (lock.current || activeProbes.current || !snapshot) return
    const invalid = draft?.connections.find(connection => providerConnectionIssue(connection))
    if (invalid) {
      setCredentialIssue({connection: invalid.id, key: providerConnectionIssue(invalid)!})
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
      if (owner.current === generation) {setSnapshot(result.routing); setDraft(null); setCredentialIssue(null); setSaved(true)}
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
  return <section className="space-y-4" aria-label={t('ui.providers.title')}>
    <header className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="ui-section-title">{t('ui.providers.title')}</h3><p className="mt-1 text-xs text-muted">{t('ui.providers.description')}</p></div><Button size="sm" disabled={busy || !!draft || !snapshot} onClick={() => {setAdding(true); setSaved(false)}}><Plus className="h-4 w-4" />{t('ui.providers.add')}</Button></header>
    {error ? <p role="alert" className="text-xs text-danger">{noticeText(error)}</p> : null}
    {!snapshot ? error ? <Button size="sm" onClick={() => {setError(''); setLoadAttempt(value => value + 1)}}>{t('ui.providers.retryLoading')}</Button> : <p role="status" className="text-xs text-muted">{t('ui.providers.loading')}</p> : null}
    {adding ? <ProviderPicker choices={choices} onClose={() => setAdding(false)} onPick={id => {setAdding(false); setSaved(false); if (id === 'codex-account-pool') {setSubscription(true); return} const selected = templates.find(row => row.id === id); if (!selected) return; setDraft({id: newIdentity(), type: selected.id, name: selected.name, connections: [newProviderConnection(selected)]})}} /> : null}
    {subscription ? <CodexGatewayAccounts /> : null}
    {snapshot && !snapshot.providers.length && !subscription && !draft && !adding ? <div className="rounded-xl border border-dashed border-line p-6 text-center text-xs text-muted">{t('ui.providers.empty')}</div> : null}
    {sortProviderChoices(configuredProviders).filter(provider => provider.id !== draft?.id).map(provider => <div key={provider.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line p-4"><div className="min-w-0"><h4 className="ui-card-title break-words">{provider.name}</h4><p className="mt-1 text-xs text-muted">{t(`ui.providers.state.${providerConfigurationState(provider)}`)}</p><p className="mt-1 text-xs text-muted">{t('ui.providers.summary', {connections: provider.connections.length, models: provider.connections.reduce((count, connection) => count + connection.models.length, 0)})}</p></div><div className="flex gap-2"><Button size="sm" disabled={busy || !!draft} onClick={() => {setDraft(structuredClone(provider)); setRemoving(null); setSaved(false)}}>{t('ui.providers.edit')}</Button><Button size="sm" disabled={busy || !!draft} aria-label={t('ui.providers.removeNamed', {name: provider.name})} onClick={() => setRemoving(provider.id)}><Trash2 className="h-4 w-4" /></Button></div>{removing === provider.id ? <div className="flex w-full flex-wrap items-center gap-2 border-t border-line pt-3"><p className="w-full text-xs text-muted">{t('ui.providers.removeConfirm', {name: provider.name})}</p><Button size="sm" variant="danger" disabled={busy} onClick={() => {setRemoving(null); void persist(configuredProviders.filter(row => row.id !== provider.id))}}>{t('ui.providers.confirmRemove')}</Button><Button size="sm" disabled={busy} onClick={() => setRemoving(null)}>{t('ui.providers.cancel')}</Button></div> : null}</div>)}
    {draft && template ? <form ref={editor} onInvalid={event => {let parent = event.target instanceof HTMLElement ? event.target.parentElement : null; while (parent) {if (parent instanceof HTMLDetailsElement) parent.open = true; parent = parent.parentElement}}} autoComplete="off" onSubmit={event => event.preventDefault()} className="space-y-4 rounded-xl border border-brand/30 bg-surface p-4"><label className="block space-y-1 text-xs"><span>{t('ui.providers.supplierName')}</span><input className={field} value={draft.name} onChange={event => setDraft({...draft, name: event.target.value})} disabled={busy} /></label>
      {draft.connections.map((connection, index) => <ConnectionEditor key={connection.id} connection={connection} template={template} busy={busy} removable={draft.connections.length > 1} initiallyOpen={index === 0 || !snapshot?.providers.some(provider => provider.connections.some(row => row.id === connection.id))} stored={!!snapshot?.providers.some(provider => provider.connections.some(row => row.id === connection.id))} onProbeStateChange={pending => {activeProbes.current = Math.max(0, activeProbes.current + (pending ? 1 : -1)); setProbeCount(activeProbes.current)}} validationIssue={credentialIssue?.connection === connection.id ? credentialIssue.key : undefined} onValidation={key => setCredentialIssue({connection: connection.id, key})} onChange={next => {setDraft(current => current?.id === draft.id ? {...current, connections: current.connections.map(row => row.id === next.id ? next : row)} : current); if (credentialIssue?.connection === next.id && !providerConnectionIssue(next)) setCredentialIssue(null)}} onRemove={() => setDraft({...draft, connections: draft.connections.filter((_, position) => position !== index)})} providerId={draft.id} onRefresh={next => {if (next) setSnapshot(next)}} />)}
      <details><summary className="cursor-pointer text-xs text-muted">{t('ui.providers.additionalConnections')}</summary><div className="mt-3"><Button size="sm" disabled={busy} onClick={() => setDraft({...draft, connections: [...draft.connections, newProviderConnection(template)]})}>{t('ui.providers.addConnection')}</Button></div></details>
      <div className="sticky bottom-0 z-10 -mx-4 -mb-4 flex flex-wrap items-center gap-2 rounded-b-xl border-t border-line bg-surface px-4 py-3"><Button disabled={busy || probeCount > 0 || !draft.name.trim() || !draft.connections.length} onClick={() => void persist([...(snapshot?.providers.filter(provider => provider.id !== draft.id) ?? []), draft])}>{t('ui.providers.save')}</Button><Button disabled={busy} onClick={() => {setDraft(null); setError(''); setCredentialIssue(null)}}>{t('ui.providers.cancel')}</Button>{busy ? <SaveStatus phase="saving" /> : null}</div>
    </form> : null}
    {saved ? <SaveStatus phase="saved" /> : null}
  </section>
}

type ConnectionProps = {onProbeStateChange: (pending: boolean) => void; validationIssue?: string; onValidation: (key: string) => void; removable: boolean; initiallyOpen: boolean; connection: ProviderConnection; template: ProviderTemplate; busy: boolean; stored: boolean; providerId: string; onChange: (next: ProviderConnection) => void; onRemove: () => void; onRefresh: (snapshot: ProviderSnapshot | null) => void}
function ConnectionEditor({onProbeStateChange, validationIssue, onValidation, removable, initiallyOpen, connection, template, busy, stored, providerId, onChange, onRemove, onRefresh}: ConnectionProps) {
  const connectionRef = useRef(connection)
  connectionRef.current = connection
  const [picking, setPicking] = useState(false)
  const [selectedSearch, setSelectedSearch] = useState('')
  const [selectedPage, setSelectedPage] = useState(0)
  const available = connection.model_catalog ?? template.models
  const [manual, setManual] = useState('')
  const [justAdded, setJustAdded] = useState<string | null>(null)
  const composing = useRef(false)
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
  const endpointInvalid = !validProviderEndpoint(connection.base_url)
  const selected = connection.models.map((model, index) => ({model, index})).filter(({model}) => `${model.model} ${model.name}`.toLowerCase().includes(selectedSearch.trim().toLowerCase()))
  const currentSelectedPage = Math.min(selectedPage, Math.max(0, Math.ceil(selected.length / 10) - 1))
  const resetSelection = (models: ProviderConnection['models']) => {setSelectedPage(0); change({models})}
  const addManualModel = () => {
    const models = connectionRef.current.models
    if (busy || !validProviderModelId(manual) || models.some(row => row.model === manual.trim())) return
    const added = newProviderModel(manual.trim())
    change({models: [...models, added]}); setManual(''); setSelectedSearch(''); setJustAdded(added.id); setSelectedPage(Math.floor(models.length / 10))
  }
  return <details data-connection-id={connection.id} open={expanded} onToggle={event => setExpanded(event.currentTarget.open)} className="relative rounded-lg border border-line bg-raised p-3"><summary className="cursor-pointer pr-10"><span className="ui-card-title">{connection.name}</span><span className="ml-2 text-xs text-muted">{t('ui.providers.modelCounts', {total: available.length, selected: connection.models.length})}</span></summary>{removable ? <Button size="xs" variant="ghost" className="absolute right-3 top-3" disabled={busy} aria-label={t('ui.providers.removeConnection')} onClick={onRemove}><Trash2 className="h-4 w-4" /></Button> : null}<div className="mt-3 space-y-3">
    <label className="block space-y-1 text-xs"><span>{t('ui.providers.endpoint')}</span><input className={field} value={connection.base_url} disabled={busy} required={template.id === 'custom' || !!connection.protocol} autoComplete="off" spellCheck={false} inputMode="url" aria-invalid={endpointInvalid} ref={element => element?.setCustomValidity(endpointInvalid ? t('ui.providers.invalidEndpoint') : '')} name={`provider-endpoint-${connection.id}`} placeholder={t('ui.providers.endpointAutomatic')} onChange={event => change({base_url: event.target.value})} /></label>
    {endpointInvalid ? <p role="alert" className="text-xs text-danger">{t('ui.providers.invalidEndpoint')}</p> : null}
    {!connection.base_url && !connection.protocol ? <p className="break-all text-xs text-muted">{t('ui.providers.endpointInherited')}{[...new Set(template.models.map(model => model.baseUrl).filter(Boolean))].map(url => <span className="block" key={url}>{url}</span>)}</p> : null}
    <label className="block space-y-1 text-xs"><span>{t('ui.providers.authentication')}</span><select className={field} value={connection.auth.type} disabled={busy} onChange={event => change({auth: {type: event.target.value, env: connection.auth.env}, keys: event.target.value === 'api_key' ? connection.keys.length ? connection.keys : [newProviderKey()] : []})}>
      {template.api_key ? <option value="api_key">{t('ui.providers.apiKey')}</option> : null}{template.oauth ? <option value="oauth">{t('ui.providers.oauth')}</option> : null}<option value="environment">{t('ui.providers.environment')}</option>{template.id === 'opencode-local' ? <option value="opencode_local">{t('ui.providers.localAuth')}</option> : null}
    </select></label>
    {connection.auth.type === 'api_key' ? <div className="space-y-2"><p className="text-xs text-muted">{t('ui.providers.sharedKeys')}</p>{connection.keys.map((key, index) => <div key={key.id} className="flex flex-wrap items-center gap-2"><label className="min-w-0 flex-1 text-xs"><span>{t('ui.providers.keyNumber', {number: index + 1})}</span><ProviderKeyInput identity={key.id} value={key.value ?? ''} mask={key.mask ?? ''} disabled={busy} onChange={value => change({keys: connection.keys.map(row => row.id === key.id ? {...row, value} : row)})} /></label><Switch checked={key.enabled} label={t('ui.providers.keyEnabled')} disabled={busy} onChange={enabled => change({keys: connection.keys.map(row => row.id === key.id ? {...row, enabled} : row)})} /><Button size="xs" disabled={busy || index === 0} aria-label={t('ui.providers.moveUp')} onClick={() => change({keys: moveProviderItem(connection.keys, index, -1)})}><ChevronUp className="h-4 w-4" /></Button><Button size="xs" disabled={busy || index === connection.keys.length - 1} aria-label={t('ui.providers.moveDown')} onClick={() => change({keys: moveProviderItem(connection.keys, index, 1)})}><ChevronDown className="h-4 w-4" /></Button><Button size="xs" disabled={busy || connection.keys.length === 1} aria-label={t('ui.providers.removeKey')} onClick={() => change({keys: connection.keys.filter(row => row.id !== key.id)})}><Trash2 className="h-4 w-4" /></Button>{key.available === false ? <span className="text-xs text-warn">{t('ui.providers.unavailable')}</span> : null}{key.in_use ? <span className="text-xs text-brand">{t('ui.providers.inUse')}</span> : null}</div>)}<Button size="xs" disabled={busy} onClick={() => change({keys: [...connection.keys, newProviderKey()]})}>{t('ui.providers.addKey')}</Button>{stored ? <Button size="xs" disabled={busy} onClick={() => {void runtimeRequest<ProviderSnapshot>('/connection/reset', 'POST', {connection_id: connection.id}).then(onRefresh).catch(failure => setError(failureText(failure)))}}>{t('ui.providers.resetKeys')}</Button> : null}</div> : null}
    {(connection.auth.type === 'environment' || ['amazon-bedrock', 'google-vertex', 'azure', 'cloudflare-ai-gateway', 'cloudflare-workers-ai'].includes(template.id)) ? <div className="space-y-2">{(template.env ?? []).map(name => <label key={name} className="block text-xs">{name}<input className={field} type={/KEY|TOKEN|SECRET/.test(name) ? "password" : "text"} autoComplete="off" value={connection.auth.env[name] ?? ''} disabled={busy} onChange={event => change({auth: {...connection.auth, env: {...connection.auth.env, [name]: event.target.value}}})} /></label>)}</div> : null}
    {connection.auth.type === 'environment' ? <p className="text-xs text-muted">{t('ui.providers.environmentHint')}</p> : null}
    {connection.auth.type === 'opencode_local' ? <p className="text-xs text-muted">{t('ui.providers.localHint')}</p> : null}
    {connection.auth.type === 'oauth' ? <ProviderAuthorization providerId={providerId} connection={connection} stored={stored} active={active} onRefresh={onRefresh} providerType={template.id} onAuthorized={id => change({auth: {...connection.auth, configured: true, authorization_id: id}})} /> : null}
    <details><summary className="cursor-pointer text-xs text-muted">{t('ui.providers.advanced')}</summary><div className="mt-3 space-y-3"><label className="block text-xs">{t('ui.providers.connectionName')}<input className={field} value={connection.name} disabled={busy} onChange={event => change({name: event.target.value})} /></label><label className="block text-xs">{t('ui.providers.protocol')}<select className={field} value={connection.protocol} disabled={busy} onChange={event => change({protocol: event.target.value})}><option value="">{t('ui.providers.nativeProtocol')}</option><option value="openai-completions">OpenAI Chat Completions</option><option value="openai-responses">OpenAI Responses</option><option value="anthropic-messages">Anthropic Messages</option></select></label><Switch checked={connection.enabled} label={t('ui.providers.connectionEnabled')} disabled={busy} onChange={enabled => change({enabled})} /><JsonField label={t('ui.providers.headers')} value={connection.headers ?? {}} disabled={busy} onChange={value => change({headers: value as Record<string, string>})} /><JsonField label={t('ui.providers.compat')} value={connection.compat ?? {}} disabled={busy} onChange={value => change({compat: value as Record<string, unknown>})} />{(['timeout_ms', 'max_retries'] as const).map(name => <label key={name} className="block text-xs">{t(`ui.providers.${name}`)}<input className={field} type="number" min={name === 'max_retries' ? '0' : '1'} value={connection[name] ?? (name === 'max_retries' ? 2 : 300000)} disabled={busy} onChange={event => change({[name]: Number(event.target.value)})} /></label>)}</div></details>
    <div className="space-y-2">{validationIssue || error ? <p role="alert" className="text-xs text-danger">{noticeText(validationIssue ? {key: validationIssue} : error)}</p> : null}{catalogChanges !== null ? <p role="status" className="text-xs text-muted">{t('ui.providers.catalogUpdated', catalogChanges)}</p> : null}{fallback && !error ? <p role="status" className="text-xs text-warn">{t('ui.providers.discoverFallback')}</p> : null}<div className="flex flex-wrap items-center justify-between gap-2"><h5 className="ui-group-title">{t('ui.providers.models')}</h5><Button size="xs" disabled={busy || loading || endpointInvalid} onClick={() => void probe()}>{t(loading ? 'ui.providers.probing' : 'ui.providers.discover')}</Button></div>
      <p className="text-xs text-muted">{t('ui.providers.modelCounts', {total: available.length, selected: connection.models.length})}</p>
      {available.length ? <Button size="sm" disabled={busy} onClick={() => setPicking(true)}>{t('ui.providers.chooseModels', {count: available.length})}</Button> : <p role="status" className="text-xs text-muted">{t('ui.providers.emptyCatalog')}</p>}
      {picking ? <ModelPicker context={connection.name} available={available} catalog={template.models} selected={connection.models} onClose={() => setPicking(false)} onConfirm={models => {setPicking(false); setSelectedSearch(''); resetSelection(models)}} /> : null}
      <div className="flex gap-2"><input className={field} aria-label={t('ui.providers.manualModel')} placeholder={t('ui.providers.manualModel')} autoComplete="off" name={`provider-model-${connection.id}`} spellCheck={false} autoCapitalize="none" data-lpignore="true" data-1p-ignore="true" aria-invalid={!!manual.trim() && !validProviderModelId(manual)} value={manual} onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}} onKeyDown={event => {if (event.key !== 'Enter') return; event.preventDefault(); if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229 || busy || !validProviderModelId(manual) || connection.models.some(row => row.model === manual.trim())) return; addManualModel()}} onChange={event => setManual(event.target.value)} /><Button size="sm" disabled={busy || !validProviderModelId(manual) || connection.models.some(row => row.model === manual.trim())} onClick={addManualModel}>{t('ui.providers.addModel')}</Button></div>
      {!!manual.trim() && !validProviderModelId(manual) ? <p role="alert" className="text-xs text-danger">{t('ui.providers.invalidModelId')}</p> : null}
      {connection.models.length > 10 || selectedSearch ? <input className={field} aria-label={t('ui.providers.searchSelectedModels')} placeholder={t('ui.providers.searchSelectedModels')} autoComplete="off" value={selectedSearch} onChange={event => {setSelectedSearch(event.target.value); setSelectedPage(0)}} /> : null}
      <div role="group" aria-label={t('ui.providers.selectedModels', {count: connection.models.length})} className="space-y-2">
      {selected.slice(currentSelectedPage * 10, (currentSelectedPage + 1) * 10).map(({model, index}) => <div key={model.id} className="space-y-2 border-t border-line pt-2"><div className="flex items-center gap-2"><span className="min-w-0 flex-1 break-all text-xs">{model.name || model.model}</span><Button size="xs" disabled={busy || index === 0} aria-label={t('ui.providers.moveUp')} onClick={() => change({models: moveProviderItem(connection.models, index, -1)})}><ChevronUp className="h-4 w-4" /></Button><Button size="xs" disabled={busy || index === connection.models.length - 1} aria-label={t('ui.providers.moveDown')} onClick={() => change({models: moveProviderItem(connection.models, index, 1)})}><ChevronDown className="h-4 w-4" /></Button><Button size="xs" disabled={busy} aria-label={t('ui.providers.removeModel')} onClick={() => change({models: connection.models.filter(row => row.id !== model.id)})}><Trash2 className="h-4 w-4" /></Button></div><details open={model.id === justAdded}><summary className="cursor-pointer text-xs text-muted">{t('ui.providers.modelOptions')}</summary><div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">{(['model', 'name', 'context_window', 'max_output_tokens'] as const).map(name => <label key={name} className="text-xs"><span>{t(name === 'name' ? 'ui.providers.modelName' : `ui.providers.${name}`)}</span><input className={field} value={model[name] ?? ''} placeholder={name === 'context_window' || name === 'max_output_tokens' ? t('ui.providers.metadataUnknown') : undefined} disabled={busy} type={name === 'model' || name === 'name' ? 'text' : 'number'} min="1" onChange={event => change({models: connection.models.map(row => row.id === model.id ? {...row, [name]: name === 'model' || name === 'name' ? event.target.value : event.target.value ? Number(event.target.value) : undefined} : row)})} /></label>)}</div><div className="mt-2 flex gap-3 text-xs">{['text', 'image', 'video'].map(modality => <label key={modality} className="flex gap-1"><input type="checkbox" checked={model.input.includes(modality)} disabled={busy || model.input.length === 1 && model.input.includes(modality)} onChange={event => change({models: connection.models.map(row => row.id === model.id ? {...row, input: event.target.checked ? [...row.input, modality] : row.input.filter(value => value !== modality)} : row)})} />{t(`ui.providers.${modality}`)}</label>)}</div><p className="mt-2 text-xs text-muted">{t('ui.providers.metadataHint')}</p><ReasoningEditor value={model.reasoning_efforts} disabled={busy} onChange={value => change({models: connection.models.map(row => row.id === model.id ? {...row, reasoning_efforts: value} : row)})} /></details></div>)}
      </div>
      {selectedSearch && !selected.length ? <p role="status" className="text-xs text-muted">{t('ui.providers.noSelectedModels')}</p> : null}
      <ModelPagination label={t('ui.providers.selectedModels', {count: connection.models.length})} total={selected.length} page={currentSelectedPage} size={10} onPage={setSelectedPage} />
    </div>
  </div></details>
}

function ProviderKeyInput({identity, value, mask, disabled, onChange}: {identity: string; value: string; mask: string; disabled: boolean; onChange: (value: string) => void}) {
  const [editing, setEditing] = useState(false)
  const [revealed, setRevealed] = useState(false)
  const masked = maskProviderKey(value)
  return <div className="relative"><input className={field + ' pr-10'} type={editing && !revealed ? 'password' : 'text'} autoComplete="new-password" name={`provider-credential-${identity}`} data-lpignore="true" data-1p-ignore="true" spellCheck={false} autoCapitalize="none" value={editing || revealed ? value : masked} placeholder={mask} required={!mask} disabled={disabled} onFocus={() => setEditing(true)} onBlur={() => {setEditing(false); setRevealed(false)}} onChange={event => {if (editing || revealed) onChange(event.target.value)}} />{value ? <button type="button" aria-label={t(revealed ? 'ui.providers.hideKey' : 'ui.providers.showKey')} className="absolute right-2 top-2 text-muted" onMouseDown={event => event.preventDefault()} onClick={() => setRevealed(!revealed)}>{revealed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button> : null}</div>
}

function ReasoningEditor({value, disabled, onChange}: {value: Record<string, string | null> | false | undefined; disabled: boolean; onChange: (value: Record<string, string | null> | false | undefined) => void}) {
  const enabled = value !== false && value !== undefined
  return <div className="mt-3 space-y-2"><label className="block text-xs">{t('ui.providers.reasoningMode')}<select className={field} aria-label={t('ui.providers.reasoningMode')} disabled={disabled} value={value === undefined ? 'inherit' : value === false ? 'disabled' : 'custom'} onChange={event => onChange(event.target.value === 'inherit' ? undefined : event.target.value === 'disabled' ? false : {off: null, low: 'low', medium: 'medium', high: 'high'})}><option value="inherit">{t('ui.providers.inherit')}</option><option value="disabled">{t('ui.providers.disableReasoning')}</option><option value="custom">{t('ui.providers.customReasoning')}</option></select></label>{enabled ? Object.entries(value).map(([level, parameter]) => <label key={level} className="flex items-center gap-2 text-xs"><span className="w-16">{level}</span><input className={field} disabled={disabled} aria-label={t('ui.providers.reasoningParameter', {level})} value={parameter ?? ''} onChange={event => onChange({...value, [level]: event.target.value || null})} /><Button size="xs" disabled={disabled} aria-label={t('ui.providers.removeReasoning', {level})} onClick={() => {const next = {...value}; delete next[level]; onChange(next)}}><Trash2 className="h-4 w-4" /></Button></label>) : null}{enabled ? <select className={field} disabled={disabled} value="" aria-label={t('ui.providers.addReasoning')} onChange={event => onChange({...value, [event.target.value]: event.target.value})}><option value="">{t('ui.providers.addReasoning')}</option>{['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'].filter(level => !(level in value)).map(level => <option key={level} value={level}>{level}</option>)}</select> : null}</div>
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
  return <div className="space-y-2 text-xs"><p className="text-muted">{t(connection.auth.configured ? 'ui.providers.authorized' : 'ui.providers.loginHint')}</p><Button size="sm" disabled={busy || state?.status === 'pending'} onClick={() => void operate('/connection/authorization', 'POST', {provider_id: providerId, connection_id: connection.id, draft: {id: providerId, type: providerType, name: providerType, connections: [connection]}})}>{t('ui.providers.login')}</Button>{state?.status === 'pending' ? <Button size="sm" disabled={busy} onClick={() => void operate(`/connection/authorization/${state.id}`, 'DELETE')}>{t('ui.providers.cancel')}</Button> : null}{state?.events.map((event, index) => <p key={index}>{event.message}{event.url || event.verificationUri ? <a className="text-brand underline" href={event.url || event.verificationUri} target="_blank" rel="noreferrer">{t('ui.providers.openLogin')}</a> : null}{event.userCode ? <code className="ml-2">{event.userCode}</code> : null}</p>)}{state?.prompt ? <label className="block">{state.prompt.message}{state.prompt.options ? <select className={field} value={answer} disabled={busy} onChange={event => setAnswer(event.target.value)}><option value="" />{state.prompt.options.map(row => <option key={row.id} value={row.id}>{row.label}</option>)}</select> : <input className={field} disabled={busy} type={state.prompt.type === 'secret' ? 'password' : 'text'} value={answer} onChange={event => setAnswer(event.target.value)} />}<Button size="sm" disabled={busy || !answer} onClick={() => void operate(`/connection/authorization/${state.id}/answer`, 'POST', {value: answer})}>{t('ui.providers.continue')}</Button></label> : null}{state?.error || error ? <p role="alert" className="text-danger">{state?.error || noticeText(error)}</p> : null}</div>
}
