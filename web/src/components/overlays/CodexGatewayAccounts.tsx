import {useEffect, useRef, useState} from 'react'
import type {ResourceText} from '@/types'
import {t, useLocale} from '@/i18n'
import {useAppStore} from '@/store/app'
import {runtimeRequest} from '@/lib/http'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import {Button} from '@/components/ui/primitives'
import {Switch} from './ExtensionCatalogControls'
import {usePageVisible} from '@/hooks/usePageVisible'
type GatewayAccount = { id: string; enabled: boolean; logged_in: boolean; email: string; pending: boolean; duplicate: boolean; available: boolean; in_use: boolean; fresh: boolean; windows: Array<{used_percent: number; window_minutes: number; resets_at: number}>; error_key: string | null; auth_error: string }
type GatewayState = { current_account: string; auto_rotate: boolean; enabled: boolean; threshold: number; waiting: number; accounts: GatewayAccount[] }

export function CodexGatewayAccounts() {
  useLocale()
  const visible = usePageVisible()
  const [state, setState] = useState<GatewayState | null>(null)
  const [desktop, setDesktop] = useState<{enabled: boolean; config_path: string} | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const owner = useRef(0)
  const mounted = useRef(false)
  const operationLock = useRef(false)
  const readController = useRef<AbortController | null>(null)
  const [authorizationUrl, setAuthorizationUrl] = useState('')
  const read = async () => {
    if (!mounted.current) return
    readController.current?.abort()
    const controller = new AbortController()
    readController.current = controller
    const generation = owner.current
    try {
      const [next, integration, catalog] = await Promise.all([
        runtimeRequest<GatewayState>('/codex-gateway', 'GET', undefined, {signal: controller.signal}),
        runtimeRequest<{enabled: boolean; config_path: string}>('/codex-gateway/desktop', 'GET', undefined, {signal: controller.signal}),
        runtimeRequest<{models: import('@/types').ModelInfo[]; active: string}>('/models', 'GET', undefined, {signal: controller.signal}),
      ])
      if (generation === owner.current && !controller.signal.aborted) {
        setState(next); setDesktop(integration); setError('')
        useAppStore.setState({models: catalog.models, defaultModel: catalog.active})
      }
    } catch (failure) {if (generation === owner.current && !controller.signal.aborted) setError(failureText(failure))}
  }
  useEffect(() => {
    ++owner.current; mounted.current = visible
    if (!visible) return
    void read()
    const timer = window.setInterval(() => {if (!operationLock.current) void read()}, 30_000)
    return () => {mounted.current = false; ++owner.current; readController.current?.abort(); window.clearInterval(timer)}
  }, [visible])
  const operate = async (key: string, operation: () => Promise<unknown>) => {
    if (operationLock.current || !mounted.current) return
    operationLock.current = true
    readController.current?.abort()
    const generation = owner.current
    setBusy(key); setError('')
    try {await operation(); await read()} catch (failure) {
      await read()
      if (generation === owner.current) setError(failureText(failure))
    } finally {operationLock.current = false; setBusy(null)}
  }
  const add = () => void operate('add', async () => {
    const result = await runtimeRequest<{account: string}>('/codex-gateway/accounts', 'POST', {})
    const login = await runtimeRequest<{authorization_url: string}>(`/codex-gateway/accounts/${encodeURIComponent(result.account)}/login`, 'POST', {})
    if (mounted.current) setAuthorizationUrl(login.authorization_url)
    if (mounted.current) window.open(login.authorization_url, '_blank', 'noopener,noreferrer')
  })
  return <section className="space-y-3 rounded-xl border border-line p-3" aria-label={t('ui.codexGateway.accountsTitle')}>
    <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-sm font-medium">{t('ui.codexGateway.accountsTitle')}</h3><p className="text-xs text-muted">{t('ui.codexGateway.accountsDescription')}</p></div><Button size="sm" disabled={busy !== null} onClick={add}>{t('ui.codexGateway.addAccount')}</Button></div>
    {state ? <><div className="flex flex-wrap items-center gap-2"><Switch checked={state.auto_rotate} disabled={busy !== null} label={t('ui.providers.autoRotate')} onChange={(next) => void operate('selection', () => runtimeRequest('/codex-gateway/selection', 'PATCH', {current_account: state.current_account, auto_rotate: next}))} /></div><div className="flex flex-wrap items-center gap-2 text-xs"><Switch checked={state.enabled} disabled={busy !== null} label={t('ui.codexGateway.gatewayEnabled')} onChange={(next) => void operate('gateway', () => runtimeRequest('/codex-gateway', 'PATCH', {enabled: next}))} /><span className="text-muted">{t('ui.codexGateway.threshold', {percent: state.threshold})}</span>{state.waiting ? <span className="text-brand">{t('ui.codexGateway.waiting', {count: state.waiting})}</span> : null}</div><div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-raised p-3 text-xs"><Switch checked={desktop?.enabled ?? false} disabled={busy !== null || !state.accounts.some(account => account.logged_in)} label={t('ui.codexGateway.desktopEnabled')} onChange={(next) => void operate('desktop', async () => { const result = await runtimeRequest<{enabled: boolean; config_path: string}>('/codex-gateway/desktop', 'PATCH', {enabled: next}); if (mounted.current) setDesktop(result) })} /><span className="text-muted">{t('ui.codexGateway.desktopDescription')}</span></div>
      <div className="space-y-2">{state.accounts.filter(account => account.id !== 'primary' || account.logged_in || account.pending).map(account => { const five = account.windows.find(window => window.window_minutes === 300); const reset = five ? new Date(five.resets_at * 1000).toLocaleString() : t('ui.codexGateway.unknown'); return <div key={account.id} className="rounded-lg border border-line bg-raised p-3 text-xs"><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium">{account.email || account.id}</span><span className={account.available ? 'text-ok' : 'text-danger'}>{account.logged_in ? account.available ? t('ui.codexGateway.available') : t('ui.codexGateway.blocked') : t('ui.codexGateway.notAuthorized')}</span></div><p className="mt-1 text-muted">{five ? t('ui.codexGateway.quota', {used: five.used_percent, reset}) : t('ui.codexGateway.quotaUnknown')}</p><div className="mt-2 flex flex-wrap gap-2"><Switch checked={account.enabled} disabled={busy !== null || account.id === 'primary'} label={t('ui.codexGateway.accountEnabled')} onChange={(next) => void operate(account.id, () => runtimeRequest(`/codex-gateway/accounts/${encodeURIComponent(account.id)}`, 'PATCH', {enabled: next}))} />{account.logged_in ? <Button size="xs" disabled={busy !== null || state.current_account === account.id} onClick={() => void operate('selection', () => runtimeRequest('/codex-gateway/selection', 'PATCH', {current_account: account.id, auto_rotate: state.auto_rotate}))}>{t(state.current_account === account.id ? 'ui.providers.currentAccount' : 'ui.providers.useAccount')}</Button> : null}{account.pending ? <Button size="xs" disabled={busy !== null} onClick={() => void operate(account.id, () => runtimeRequest(`/codex-gateway/accounts/${encodeURIComponent(account.id)}/cancel`, 'POST', {}))}>{t('ui.codexGateway.cancel')}</Button> : <Button size="xs" disabled={busy !== null} onClick={() => void operate(account.id, async () => { const result = await runtimeRequest<{authorization_url: string}>(`/codex-gateway/accounts/${encodeURIComponent(account.id)}/login`, 'POST', {}); if (mounted.current) setAuthorizationUrl(result.authorization_url); if (mounted.current) window.open(result.authorization_url, '_blank', 'noopener,noreferrer') })}>{t('ui.codexGateway.login')}</Button>}{account.id !== 'primary' ? <Button size="xs" disabled={busy !== null || account.pending} onClick={() => void operate(account.id, () => runtimeRequest(`/codex-gateway/accounts/${encodeURIComponent(account.id)}`, 'DELETE', {}))}>{t('ui.codexGateway.remove')}</Button> : null}</div></div> })}</div></> : <p role="status" className="text-xs text-muted">{t('ui.codexGateway.loading')}</p>}
    {authorizationUrl ? <a className="text-xs text-brand underline" href={authorizationUrl} target="_blank" rel="noreferrer">{t('ui.providers.openLogin')}</a> : null}
    {error ? <p role="alert" className="text-xs text-danger">{noticeText(error)}</p> : null}
  </section>
}
