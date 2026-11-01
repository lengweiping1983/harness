import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { ResourceError, failureText } from '@/lib/resourceError'
import { useEffect, useRef, useState } from 'react'
import { Download, Loader2, RotateCw, X } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { runtimeRequest, ApiError, isUnconfirmedWrite } from '@/lib/http'
import { installRequestId } from '@/lib/installRequestId'
import { useAppStore } from '@/store/app'
import { Button } from '@/components/ui/primitives'

import type { TerminalRuntimeSetup as TerminalSetup, TerminalRuntimeInstallation as TerminalInstallation } from '@/lib/protocol.generated'

export function TerminalSetupPanel({ active, enabled, onReady }: { active: boolean; enabled: boolean; onReady: () => void }) {
  useLocale()
  const [data, setData] = useState<TerminalSetup | null>(null)
  const [job, setJob] = useState<TerminalInstallation | null>(null)
  const [watchId, setWatchId] = useState<string | null>(null)
  const [checking, setChecking] = useState(true)
  const [saving, setSaving] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [retry, setRetry] = useState(0)
  const alive = useRef(true)
  const pending = useRef(false)
  const openAfter = useRef<string | null>(null)
  const readyCallback = useRef(onReady)
  readyCallback.current = onReady
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  function receive(next: TerminalInstallation | null) {
    setJob(next)
    setWatchId(next?.status === 'running' ? next.id : null)
    if (next?.status === 'succeeded') {
      setData(previous => previous ? { ...previous, ready: true, error: null } : previous)
    } else if (next && next.status !== 'running') openAfter.current = null
  }

  useEffect(() => {
    if (!active || !enabled || job?.status !== 'succeeded' || openAfter.current !== job.id) return
    openAfter.current = null
    useAppStore.getState().toast({ tone: 'success', title: {key: 'ui.terminalSetup.ready'} })
    readyCallback.current()
  }, [active, enabled, job])

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    let current = true
    setChecking(true)
    void runtimeRequest<TerminalSetup>('/terminal/setup', 'GET', undefined, { signal: controller.signal }).then(next => {
      if (!current) return
      setData(next); receive(next.installation); setError('')
    }).catch(e => { if (current) setError(failureText(e)) })
      .finally(() => { if (current) setChecking(false) })
    return () => { current = false; controller.abort() }
  }, [active, retry])

  useEffect(() => {
    if (!active || !watchId) return
    const controller = new AbortController()
    let current = true
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const next = await runtimeRequest<{ installation: TerminalInstallation }>('/terminal/setup/install?id=' + encodeURIComponent(watchId!), 'GET', undefined, { signal: controller.signal })
        if (!current) return
        if (next.installation?.id !== watchId) throw new ResourceError({key: 'ui.http.invalidResponse'})
        receive(next.installation); setError('')
        if (next.installation.status !== 'running') return
      } catch (e) {
        if (!current) return
        setError(failureText(e))
        if (e instanceof ApiError && e.status === 404) { setWatchId(null); setJob(null); openAfter.current = null; return }
      }
      timer = setTimeout(() => void poll(), 800)
    }
    void poll()
    return () => { current = false; controller.abort(); clearTimeout(timer) }
  }, [active, watchId])

  async function install() {
    if (pending.current || checking || watchId || !enabled) return
    pending.current = true; setSaving(true); setError('')
    const id = installRequestId()
    openAfter.current = id
    try {
      const next = await runtimeRequest<{ installation: TerminalInstallation }>('/terminal/setup/install', 'POST', { request_id: id })
      if (next.installation?.id !== id) throw new ResourceError({key: 'ui.http.invalidResponse'})
      if (alive.current) receive(next.installation)
    } catch (e) {
      if (!alive.current) return
      if (e instanceof ApiError && !isUnconfirmedWrite(e.failure)) {
        openAfter.current = null; setError(failureText(e)); setRetry(value => value + 1)
      } else {
        setError({key: 'ui.terminalSetup.uncertain'}); setWatchId(id)
      }
    } finally {
      pending.current = false
      if (alive.current) setSaving(false)
    }
  }

  async function cancel() {
    if (!watchId || cancelling) return
    setCancelling(true); openAfter.current = null
    try {
      const next = await runtimeRequest<{ installation: TerminalInstallation }>('/terminal/setup/install', 'DELETE', { id: watchId })
      if (next.installation?.id !== watchId) throw new ResourceError({key: 'ui.http.invalidResponse'})
      if (alive.current) receive(next.installation)
    } catch (e) { if (alive.current) setError(failureText(e)) }
    finally { if (alive.current) setCancelling(false) }
  }

  if (!enabled || data?.ready || data?.supported === false) return null
  const running = saving || !!watchId || job?.status === 'running'
  return <div className="border-b border-line bg-brand/5 px-3 py-3" data-terminal-setup="true">
    <div className="flex items-start gap-2">
      <Download className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium">{t('ui.terminalSetup.title')}</p>
        <p className="mt-1 text-xs leading-5 text-muted">{t('ui.terminalSetup.description')}</p>
        {checking || running ? <p role="status" className="mt-2 flex items-center gap-2 text-xs"><Loader2 className="h-3 w-3 animate-spin" />{checking ? t('ui.terminalSetup.checking') : job?.message ? noticeText(job.message) : t('ui.terminalSetup.checking')}</p>
          : job?.message ? <p role="status" className="mt-2 text-xs text-muted">{noticeText(job.message)}</p> : null}
        {error || data?.error ? <p role="alert" className="mt-2 text-xs text-danger">{error ? noticeText(error) : data?.error ? noticeText(data.error) : null}</p> : null}
        <div className="mt-2 flex flex-wrap gap-2">
          {running ? <Button size="sm" disabled={saving || cancelling} onClick={() => void cancel()}><X className="h-3 w-3" />{t('ui.terminalSetup.cancel')}</Button>
            : <Button size="sm" disabled={checking || !data} onClick={() => void install()}><Download className="h-3 w-3" />{job?.status === 'failed' || job?.status === 'cancelled' ? t('ui.terminalSetup.retry') : t('ui.terminalSetup.install')}</Button>}
          {!running && error ? <Button size="sm" disabled={checking} onClick={() => setRetry(value => value + 1)}><RotateCw className="h-3 w-3" />{t('ui.terminalSetup.refresh')}</Button> : null}
        </div>
      </div>
    </div>
  </div>
}
