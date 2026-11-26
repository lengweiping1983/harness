import {useEffect, useRef, useState} from 'react'
import type {ModelCatalog, ModelInfo, ResourceText} from '@/types'
import {hasTranslation, t, useLocale} from '@/i18n'
import {useAppStore} from '@/store/app'
import {runtimeRequest} from '@/lib/http'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import {ModelPreferences} from './ModelPreferences'
import {Button, RefreshButton} from '@/components/ui/primitives'
import {Switch} from './ExtensionCatalogControls'
import {usePageVisible} from '@/hooks/usePageVisible'
type GatewayAccount = { id: string; enabled: boolean; logged_in: boolean; email: string; pending: boolean; duplicate: boolean; available: boolean; in_use: boolean; fresh: boolean; windows: Array<{used_percent: number; window_minutes: number; resets_at: number}>; error_key: string | null; auth_error: string }
export type GatewayState = { current_account: string; auto_rotate: boolean; enabled: boolean; threshold: number; waiting: number; accounts: GatewayAccount[] }

function canRefresh(account: GatewayAccount) {
  return account.enabled && account.logged_in && !account.pending && !account.duplicate
}

type AccountCardProps = {
  state: GatewayState
  onStateChange: (next: GatewayState) => void
  models: ModelInfo[]
  editing: boolean
  onEditChange: (editing: boolean) => void
}

export function CodexGatewayAccounts({state, onStateChange, models, editing, onEditChange}: AccountCardProps) {
  useLocale()
  const visible = usePageVisible()
  const [refreshErrors, setRefreshErrors] = useState<ResourceText[]>([])
  const attempted = useRef(new Set<string>())
  const [desktop, setDesktop] = useState<{enabled: boolean; config_path: string} | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const owner = useRef(0)
  const mounted = useRef(false)
  const operationLock = useRef(false)
  const readController = useRef<AbortController | null>(null)
  const [authorizationUrl, setAuthorizationUrl] = useState('')
  const catalogGeneration = useRef(0)
  const read = async (initial = false) => {
    const catalogOwner = catalogGeneration.current
    if (!mounted.current) return
    readController.current?.abort()
    const controller = new AbortController()
    readController.current = controller
    const generation = owner.current
    try {
      const [next, integration, catalog] = await Promise.all([
        initial ? null : runtimeRequest<GatewayState>('/codex-gateway', 'GET', undefined, {signal: controller.signal}),
        runtimeRequest<{enabled: boolean; config_path: string}>('/codex-gateway/desktop', 'GET', undefined, {signal: controller.signal}),
        initial ? null : runtimeRequest<ModelCatalog>('/models', 'GET', undefined, {signal: controller.signal}),
      ])
      if (generation === owner.current && !controller.signal.aborted) {
        if (next) onStateChange(next)
        setDesktop(integration); setError('')
        if (catalog && catalogOwner === catalogGeneration.current) useAppStore.setState({models: catalog.models, defaultModel: catalog.active})
      }
    } catch (failure) {if (generation === owner.current && !controller.signal.aborted) setError(failureText(failure))}
  }
  useEffect(() => {
    ++owner.current; mounted.current = visible
    if (!visible) return
    // 页面打开和恢复时父组件已负责读取账号和模型，此处只补充桌面集成状态。
    void read(true)
    const timer = window.setInterval(() => {if (!operationLock.current) void read()}, 30_000)
    return () => {mounted.current = false; ++owner.current; readController.current?.abort(); window.clearInterval(timer)}
  }, [visible])
  const operate = async (key: string, operation: () => Promise<unknown>) => {
    if (operationLock.current || !mounted.current) return
    operationLock.current = true
    readController.current?.abort()
    const generation = owner.current
    setBusy(key); setError('')
    try {await operation(); if (generation === owner.current) await read()} catch (failure) {
      if (generation === owner.current) await read()
      if (generation === owner.current) setError(failureText(failure))
    } finally {operationLock.current = false; setBusy(null)}
  }
  const refresh = async (accounts: GatewayAccount[]) => {
    if (!accounts.length || operationLock.current || !mounted.current) return
    operationLock.current = true
    ++catalogGeneration.current
    const generation = owner.current
    setBusy('models'); setRefreshErrors([])
    try {
      const results = await Promise.allSettled(accounts.map(account => {
        attempted.current.add(account.id)
        return runtimeRequest(`/codex-gateway/accounts/${encodeURIComponent(account.id)}/models`, 'POST')
      }))
      if (generation !== owner.current) {
        if (mounted.current) await read()
        return
      }
      setRefreshErrors(results.flatMap((result, index) => result.status === 'rejected' ? [
        {template: {key: 'ui.chatgpt.catalogRefreshFailed', params: {account: accounts[index].email || accounts[index].id, reason: failureText(result.reason)}}},
      ] : []))
      const catalog = await runtimeRequest<ModelCatalog>('/models')
      if (generation === owner.current) useAppStore.setState({models: catalog.models, defaultModel: catalog.active})
    } catch (failure) {
      if (generation === owner.current) setRefreshErrors(previous => [...previous, failureText(failure)])
    } finally {
      operationLock.current = false
      setBusy(null)
    }
  }
  useEffect(() => {
    const eligible = state.accounts.filter(canRefresh)
    const ids = new Set(eligible.map(account => account.id))
    for (const id of attempted.current) if (!ids.has(id)) attempted.current.delete(id)
    if (visible && !busy) void refresh(eligible.filter(account => !attempted.current.has(account.id)))
  }, [state, visible, busy])
  const add = () => void operate('add', async () => {
    const result = await runtimeRequest<{account: string}>('/codex-gateway/accounts', 'POST', {})
    const login = await runtimeRequest<{authorization_url: string}>(`/codex-gateway/accounts/${encodeURIComponent(result.account)}/login`, 'POST', {})
    if (mounted.current) setAuthorizationUrl(login.authorization_url)
    if (mounted.current) window.open(login.authorization_url, '_blank', 'noopener,noreferrer')
  })
  const loggedIn = state.accounts.filter(account => account.logged_in)
  const configurationState = loggedIn.some(account => account.enabled) ? 'configured' : loggedIn.length ? 'disabled' : 'incomplete'
  const providerName = t('ui.chatgpt.title')
  return <section data-provider-id="codex-account-pool" className="overflow-hidden rounded-xl border border-line" aria-label={providerName}>
    <header className="flex flex-wrap items-center justify-between gap-3 p-4">
      <div className="min-w-0 flex-1"><h4 className="ui-card-title break-words">{providerName}</h4>
        <p className="mt-1 text-xs text-muted">{t('ui.providers.summary', {state: t(`ui.providers.state.${configurationState}`), models: models.length})}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <RefreshButton size="sm" label={t('ui.chatgpt.refresh')} spinning={busy === 'models'} disabled={busy !== null || !state.accounts.some(canRefresh)} onClick={() => void refresh(state.accounts.filter(canRefresh))} />
        <Button size="sm" aria-expanded={editing} onClick={() => onEditChange(!editing)}>{t(editing ? 'ui.chatgpt.closeAccounts' : 'ui.providers.edit')}</Button>
      </div>
    </header>
    {refreshErrors.map((issue, index) => <p key={index} role="alert" className="px-4 pb-3 text-xs text-danger">{noticeText(issue)}</p>)}
    {editing ? <div className="space-y-3 border-t border-line p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><div><h5 className="ui-group-title">{t('ui.codexGateway.accountsTitle')}</h5><p className="text-xs text-muted">{t('ui.codexGateway.accountsDescription')}</p></div><Button size="sm" disabled={busy !== null} onClick={add}>{t('ui.codexGateway.addAccount')}</Button></div>
    <div className="flex flex-wrap items-center gap-2"><Switch checked={state.auto_rotate} disabled={busy !== null} label={t('ui.providers.autoRotate')} onChange={(next) => void operate('selection', () => runtimeRequest('/codex-gateway/selection', 'PATCH', {current_account: state.current_account, auto_rotate: next}))} /></div><div className="flex flex-wrap items-center gap-2 text-xs"><Switch checked={state.enabled} disabled={busy !== null} label={t('ui.codexGateway.gatewayEnabled')} onChange={(next) => void operate('gateway', () => runtimeRequest('/codex-gateway', 'PATCH', {enabled: next}))} /><span className="text-muted">{t('ui.codexGateway.threshold', {percent: state.threshold})}</span>{state.waiting ? <span className="text-brand">{t('ui.codexGateway.waiting', {count: state.waiting})}</span> : null}</div><div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-raised p-3 text-xs"><Switch checked={desktop?.enabled ?? false} disabled={busy !== null || !desktop || !state.accounts.some(account => account.logged_in)} label={t('ui.codexGateway.desktopEnabled')} onChange={(next) => void operate('desktop', async () => { const result = await runtimeRequest<{enabled: boolean; config_path: string}>('/codex-gateway/desktop', 'PATCH', {enabled: next}); if (mounted.current) setDesktop(result) })} /><span className="text-muted">{t('ui.codexGateway.desktopDescription')}</span></div>
      <div className="space-y-2">{state.accounts.filter(account => account.id !== 'primary' || account.logged_in || account.pending).map(account => { const five = account.windows.find(window => window.window_minutes === 300); const reset = five ? new Date(five.resets_at * 1000).toLocaleString() : t('ui.codexGateway.unknown'); return <div key={account.id} className="rounded-lg border border-line bg-raised p-3 text-xs"><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium">{account.email || account.id}</span><span className={account.available ? 'text-ok' : 'text-danger'}>{account.logged_in ? account.available ? t('ui.codexGateway.available') : t('ui.codexGateway.blocked') : t('ui.codexGateway.notAuthorized')}</span></div><p className="mt-1 text-muted">{five ? t('ui.codexGateway.quota', {used: five.used_percent, reset}) : t('ui.codexGateway.quotaUnknown')}</p>{account.error_key && hasTranslation(account.error_key) ? <p className="mt-1 text-danger">{t(account.error_key)}</p> : null}<div className="mt-2 flex flex-wrap gap-2"><Switch checked={account.enabled} disabled={busy !== null || account.id === 'primary'} label={t('ui.codexGateway.accountEnabled')} onChange={(next) => void operate(account.id, () => runtimeRequest(`/codex-gateway/accounts/${encodeURIComponent(account.id)}`, 'PATCH', {enabled: next}))} />{account.logged_in ? <Button size="xs" disabled={busy !== null || state.current_account === account.id} onClick={() => void operate('selection', () => runtimeRequest('/codex-gateway/selection', 'PATCH', {current_account: account.id, auto_rotate: state.auto_rotate}))}>{t(state.current_account === account.id ? 'ui.providers.currentAccount' : 'ui.providers.useAccount')}</Button> : null}{account.pending ? <Button size="xs" disabled={busy !== null} onClick={() => void operate(account.id, () => runtimeRequest(`/codex-gateway/accounts/${encodeURIComponent(account.id)}/cancel`, 'POST', {}))}>{t('ui.codexGateway.cancel')}</Button> : <Button size="xs" disabled={busy !== null} onClick={() => void operate(account.id, async () => { const result = await runtimeRequest<{authorization_url: string}>(`/codex-gateway/accounts/${encodeURIComponent(account.id)}/login`, 'POST', {}); if (mounted.current) setAuthorizationUrl(result.authorization_url); if (mounted.current) window.open(result.authorization_url, '_blank', 'noopener,noreferrer') })}>{t('ui.codexGateway.login')}</Button>}{account.id !== 'primary' ? <Button size="xs" disabled={busy !== null || account.pending} onClick={() => void operate(account.id, () => runtimeRequest(`/codex-gateway/accounts/${encodeURIComponent(account.id)}`, 'DELETE', {}))}>{t('ui.codexGateway.remove')}</Button> : null}</div></div> })}</div>
    {authorizationUrl ? <a className="text-xs text-brand underline" href={authorizationUrl} target="_blank" rel="noreferrer">{t('ui.providers.openLogin')}</a> : null}
    </div> : null}
    {models.length > 0 ? <div className="space-y-3 border-t border-line px-4 pb-4 pt-3"><ModelPreferences models={models} grouped={false} heading={false} modelIdentifier={model => model.id.slice('chatgpt/'.length)} /></div> : null}
    {error ? <p role="alert" className="text-xs text-danger">{noticeText(error)}</p> : null}
  </section>
}
