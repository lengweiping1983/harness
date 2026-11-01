/**
 * 当前产品页面未挂载此组件；保留为 /chatgpt 账户流程的浏览器回归挂载点。
 * 夹具：settings-feedback-language、auxiliary-read-lifecycle、hook-account-copy、
 * chatgpt-catalog-ownership（web/tests/*-smoke.mjs）。不等同于供应商授权入口。
 */
import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { useEffect, useRef, useState } from 'react'
import { t, useLocale } from '@/i18n'
import { Button, RefreshButton } from '@/components/ui/primitives'
import { ApiError, runtimeRequest } from '@/lib/http'
import { connectionChanged } from '@/lib/connectionRoutes'
import { useAppStore } from '@/store/app'
import type { ModelInfo } from '@/types'
import type { ChatGPTAccountStatus } from '@/lib/protocol.generated'
import { usePageVisible } from '@/hooks/usePageVisible'

type Account = ChatGPTAccountStatus

export function ChatGPTLogin({ onStatus }: { onStatus: (active: boolean) => void }) {
  useLocale()
  const pageVisible = usePageVisible()
  const [account, setAccount] = useState<Account | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [readError, setReadError] = useState<ResourceText>('')
  const [url, setUrl] = useState('')
  const alive = useRef(false)
  const generation = useRef(0)
  const operationLock = useRef(false)
  const refreshCatalog = async (signal?: AbortSignal) => {
    const revision = generation.current
    const catalog = await runtimeRequest<{ models: ModelInfo[]; active: string }>('/models', 'GET', undefined, {signal})
    if (alive.current && !signal?.aborted && revision === generation.current) {
      useAppStore.setState({ models: catalog.models, defaultModel: catalog.active })
      connectionChanged()
    }
  }
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    if (!pageVisible) return
    const controller = new AbortController()
    let pending = false, queued = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let previous: boolean | undefined
    const read = async () => {
      if (document.hidden || controller.signal.aborted || operationLock.current) return
      if (pending) {queued = true; return}
      pending = true
      setReadError('')
      const revision = generation.current
      try {
        const next = await runtimeRequest<Account>('/chatgpt', 'GET', undefined, { signal: controller.signal })
        if (!alive.current || controller.signal.aborted || revision !== generation.current) return
        setAccount(next); onStatus(next.logged_in)
        if (previous !== undefined && previous !== next.logged_in) await refreshCatalog(controller.signal)
        if (!alive.current || controller.signal.aborted || revision !== generation.current) return
        previous = next.logged_in
        if (!next.pending) setUrl('')
        if (next.pending && alive.current) timer = setTimeout(read, 1000)
      } catch (failure) {
        if (alive.current && !controller.signal.aborted && revision === generation.current) setReadError(failureText(failure))
      } finally {
        pending = false
        if (queued && alive.current && !controller.signal.aborted) {queued = false; clearTimeout(timer); void read()}
      }
    }
    const visible = () => { clearTimeout(timer); if (!document.hidden) void read() }
    document.addEventListener('visibilitychange', visible)
    window.addEventListener('chatgpt-account-changed', visible)
    void read()
    return () => {
      controller.abort(); clearTimeout(timer)
      document.removeEventListener('visibilitychange', visible)
      window.removeEventListener('chatgpt-account-changed', visible)
    }
  }, [onStatus, pageVisible])
  const beginOperation = () => {
    if (!alive.current || operationLock.current) return false
    operationLock.current = true
    setBusy(true); setError(''); ++generation.current
    return true
  }
  const finishOperation = () => {
    operationLock.current = false
    if (alive.current) { setBusy(false); window.dispatchEvent(new Event('chatgpt-account-changed')) }
  }
  const perform = async (action: string) => {
    if (!beginOperation()) return
    try {
      const next = await runtimeRequest<Account>(`/chatgpt/${action}`, 'POST', {})
      if (!alive.current) return
      setAccount(next); onStatus(next.logged_in)
      setUrl(next.authorization_url ?? '')
      if (action !== 'login') await refreshCatalog()
    } catch (failure) {
      if (alive.current) setError(failureText(failure))
      // 写入结果未知时只回读，不重放登录或退出。
      if (alive.current) await refreshCatalog().catch(check => { if (alive.current) setReadError(failureText(check)) })
    } finally { finishOperation() }
  }
  return <section className="space-y-3 rounded-xl border border-line p-3" aria-label={t('ui.chatgpt.title')}>
    <h3 className="text-sm font-medium">{t('ui.chatgpt.title')}</h3>
    <p className="text-xs text-muted">{t('ui.chatgpt.description')}</p>
    {!account ? !readError ? <p role="status" className="text-xs text-muted">{t('ui.chatgpt.loading')}</p> : null : <>
      <p role="status" className="text-xs">{account.logged_in ? t('ui.chatgpt.connected', {email: account.email}) : account.pending ? t('ui.chatgpt.pending') : t('ui.chatgpt.disconnected')}</p>
      {account.logged_in && account.models.length > 0 ? <label className="block text-xs">{t('ui.chatgpt.model')}
        <select className="mt-1 w-full rounded-lg border border-line bg-canvas px-3 py-2 text-xs" disabled={busy} value={account.selected} onChange={async (event) => {
          if (!beginOperation()) return
          try {
            const failure = await useAppStore.getState().setDefaultModel(event.target.value)
            if (failure) throw new ApiError(failure)
          } catch (failure) { if (alive.current) setError(failureText(failure)) }
          finally { finishOperation() }
        }}>
          {account.models.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}
        </select>
      </label> : null}
      <div className="flex flex-wrap gap-2">
        <Button disabled={busy || account.pending} onClick={() => void perform('login')}>{t('ui.chatgpt.login')}</Button>
        {account.pending ? <Button disabled={busy} onClick={() => void perform('cancel')}>{t('ui.chatgpt.cancel')}</Button> : null}
        {account.logged_in ? <>
          <RefreshButton label={t('ui.chatgpt.refresh')} disabled={busy || account.pending} onClick={() => void perform('models')} />
          <Button disabled={busy} onClick={() => void perform('logout')}>{t('ui.chatgpt.logout')}</Button>
        </> : null}
      </div>
      {url ? <a href={url} target="_blank" rel="noreferrer" className="inline-block text-xs text-brand underline">{t('ui.chatgpt.open')}</a> : null}
    </>}
    {error || account?.error ? <p role="alert" className="text-xs text-danger">{error ? noticeText(error) : account?.error}</p> : null}
    {readError ? <div className="space-y-2"><p role="alert" className="text-xs text-danger">{noticeText(readError)}</p><Button size="sm" disabled={busy} onClick={() => window.dispatchEvent(new Event('chatgpt-account-changed'))}>{t('ui.chatgpt.retryRead')}</Button></div> : null}
  </section>
}
