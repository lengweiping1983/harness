import type {ResourceText} from '@/types'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import {useEffect, useState, useId} from 'react'
import {Activity, AlertTriangle, ChevronRight, Loader2} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {runtimeRequest} from '@/lib/http'
import {browserActionLabel, browserActivityStatus, type BrowserDiagnostics, type BrowserState} from '@/lib/browser'
import {Button} from '@/components/ui/primitives'
import {usePageVisible} from '@/hooks/usePageVisible'

/** Read small status snapshots; never capture pixels or start a browser. */
export function BrowserActivityBar({sessionId, pageId, active}: {sessionId: string; pageId: string; active: boolean}) {
  useLocale()
  const visible = usePageVisible() && active
  const diagnosticsId = useId()
  const [snapshot, setSnapshot] = useState<BrowserState | null>(null)
  const [diagnostics, setDiagnostics] = useState<BrowserDiagnostics | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    setSnapshot(null); setDiagnostics(null); setError(''); setLoading(active)
    if (!visible) return
    let stopped = false, pending = false, queued = false
    const controller = new AbortController()
    const path = '/sessions/' + encodeURIComponent(sessionId) + '/browser'
    const refresh = async () => {
      if (stopped || document.hidden) return
      if (pending) {queued = true; return}
      pending = true
      try {
        const next = await runtimeRequest<BrowserState>(path, 'GET', undefined, {signal:controller.signal})
        if (stopped) return
        setSnapshot(next.active_tab === pageId ? next : null)
        if (expanded && next.active_tab === pageId) {
          const details = await runtimeRequest<BrowserDiagnostics>(path + '/diagnostics?tab_id=' + encodeURIComponent(pageId), 'GET', undefined, {signal:controller.signal})
          if (!stopped) setDiagnostics(details.page_id === pageId ? details : null)
        }
        if (!stopped) setError('')
      } catch (failure) {if (!stopped) setError(failureText(failure))}
      finally {pending = false; if (!stopped) setLoading(false); if (queued && !stopped) {queued = false; void refresh()}}
    }
    const activity = (event: Event) => {
      if ((event as CustomEvent<{session_id?: string}>).detail?.session_id === sessionId) void refresh()
    }
    const refreshVisible = () => {if (!document.hidden) void refresh()}
    window.addEventListener('harness-browser-activity', activity)
    document.addEventListener('visibilitychange', refreshVisible)
    window.addEventListener('harness-service-restored', refreshVisible)
    const timer = expanded ? setInterval(() => void refresh(), 1000) : undefined
    void refresh()
    return () => {
      stopped = true; controller.abort(); clearInterval(timer)
      window.removeEventListener('harness-browser-activity', activity)
      document.removeEventListener('visibilitychange', refreshVisible)
      window.removeEventListener('harness-service-restored', refreshVisible)
    }
  }, [sessionId, pageId, visible, expanded])
  const operation = snapshot?.activity ?? snapshot?.history[0]
  const health = diagnostics?.summary ?? snapshot?.health
  return <section className="shrink-0 border-b border-line bg-canvas text-xs" aria-label={t('browser.telemetry.title')}>
    <div className="flex min-w-0 items-center gap-2 px-3 py-1.5">
      {operation?.status === 'running' ? <Loader2 aria-hidden className="h-3 w-3 shrink-0 animate-spin text-brand"/> : <Activity aria-hidden className="h-3 w-3 shrink-0 text-muted"/>}
      <p role="status" aria-live="polite" className="min-w-0 flex-1 truncate text-muted" title={operation?.error}>
        {operation ? <>{t(operation.actor === 'agent' ? 'browser.telemetry.agent' : 'browser.telemetry.user')} · {browserActionLabel[operation.action] || operation.action} · {browserActivityStatus(operation.status)}</> : t('browser.telemetry.waiting')}
      </p>
      <Button size="xs" variant="ghost" aria-expanded={expanded} aria-controls={diagnosticsId} onClick={() => setExpanded(value => !value)}>
        {!!health?.errors && <AlertTriangle aria-hidden className="h-3 w-3 text-danger"/>}
        {t('browser.telemetry.diagnostics')}{health ? ' ' + t('browser.telemetry.diagnosticCounts', { errors: health.errors, warnings: health.warnings }) : ''}<ChevronRight aria-hidden className={'h-3 w-3 transition-transform ' + (expanded ? 'rotate-90' : '')}/>
      </Button>
    </div>
    {expanded && <div id={diagnosticsId} className="max-h-52 space-y-2 overflow-y-auto border-t border-line px-3 py-2">
      <p className="text-muted">{t('browser.telemetry.counts')}</p>
      {!diagnostics && !error && <p role="status" className="text-muted">{t(loading ? 'browser.telemetry.loading' : 'browser.telemetry.waiting')}</p>}
      {diagnostics && !diagnostics.records.length && <p className="text-muted">{t('browser.telemetry.empty')}</p>}
      {diagnostics?.records.map(record => <article key={record.id} className="rounded-md border border-line bg-surface p-2">
        <p className={record.level === 'error' ? 'text-danger' : 'text-warn'}>{record.kind}{record.status ? ` · HTTP ${record.status}` : ''}</p>
        <p className="whitespace-pre-wrap break-words font-mono text-xs">{record.message}</p>
        {record.url && <p className="mt-1 break-all text-muted">{record.url}{record.line ? ':' + record.line : ''}</p>}
      </article>)}
      <p className="text-faint">{t('browser.telemetry.evidence')}</p>
    </div>}
    {error && <p role="status" className="break-words px-3 pb-2 text-warn">{t('browser.telemetry.unavailable', {detail: noticeText(error)})}</p>}
  </section>
}
