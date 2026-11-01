import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import {useEffect, useId, useRef, useState} from 'react'
import {Button, RefreshButton} from '@/components/ui/primitives'
import {t,useLocale} from '@/i18n'
import {ApiError,runtimeRequest} from '@/lib/http'
import type {MemoryPreview, MemoryReference} from '@/lib/protocol.generated'
import {useAppStore} from '@/store/app'

function Reference({item}: {item: MemoryReference}) {
  return <li className="space-y-1">
    <p className="break-all font-mono">{item.id} · {t('ui.memory.revision', {revision: item.revision})}</p>
    <p>{t('ui.memory.preview.reason.' + item.reason)} · {t('ui.memory.preview.source', {state: t('ui.memory.preview.state.' + item.source_status)})} · {t('ui.memory.preview.derivation', {state: t('ui.memory.preview.state.' + item.derivation_status)})}</p>
  </li>
}

export function MemoryRecallPreview({sessionId}: {sessionId: string}) {
  useLocale()
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [report, setReport] = useState<MemoryPreview | null>(null)
  const controller = useRef<AbortController | null>(null)
  const generation = useRef(0)
  const mounted = useRef(false)
  const id = useId()
  useEffect(() => {
    mounted.current = true
    return () => {mounted.current = false; generation.current++; controller.current?.abort()}
  }, [])
  const load = async () => {
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    const ticket = ++generation.current
    const current = () => mounted.current && ticket === generation.current
      && useAppStore.getState().activeSessionId === sessionId
    setLoading(true); setError(''); setReport(null)
    try {
      const result = await runtimeRequest<MemoryPreview>(`/sessions/${encodeURIComponent(sessionId)}/memory/preview`,
        'GET', undefined, {signal: abort.signal})
      if (current()) {
        if (result.session_id !== sessionId) { setError({key: 'ui.http.invalidResponse'}); return }
        setReport(result)
      }
    } catch (failure) {if (current() && !abort.signal.aborted) setError(failure instanceof ApiError && failure.failure.copy ? failure.failure.copy : (failure as Error).message)}
    finally {if (current()) setLoading(false)}
  }
  const toggle = () => {
    if (open) {
      generation.current++; controller.current?.abort(); setLoading(false); setReport(null); setError('')
    } else void load()
    setOpen(!open)
  }
  return <div className="space-y-3">
    <Button size="sm" aria-expanded={open} aria-controls={id} onClick={toggle}>{t(open ? 'ui.memory.preview.hide' : 'ui.memory.preview.show')}</Button>
    {open && <section id={id} aria-label={t('ui.memory.preview.title')} aria-busy={loading} className="space-y-3 rounded-lg border border-line p-3 text-xs">
      <p className="text-muted">{t('ui.memory.preview.hint')}</p>
      <RefreshButton size="sm" label={t('ui.memory.preview.refresh')} disabled={loading} onClick={() => void load()} />
      {loading && <p role="status">{t('ui.memory.loading')}</p>}
      {error && <p role="alert" className="break-words text-danger">{noticeText(error)}</p>}
      {report && <>
        <p>{t('ui.memory.preview.usage', {used: report.used_chars, budget: report.budget})}</p>
        <p>{t('ui.memory.preview.sequence', {sequence: report.source_seq ?? '—'})}</p>
        {report.layers.map(layer => <section key={layer.scope} className="space-y-2 border-t border-line pt-3">
          <h4 className="text-sm font-medium">{t('ui.memory.scope.' + layer.scope)} · {t('ui.memory.preview.state.' + layer.status)}</h4>
          {layer.error_code && <p className="break-all text-danger">{layer.error_code}</p>}
          {layer.recall && <>
            <p>{t('ui.memory.preview.usage', {used: layer.recall.used_chars, budget: layer.recall.budget})}</p>
            <p>{t('ui.memory.preview.counts', {selected: layer.recall.selected.length, budget: layer.recall.skipped_budget.length,
              covered: layer.recall.suppressed.length, limited: layer.recall.skipped_query_limit, unmatched: layer.recall.unmatched})}</p>
            {!!layer.recall.selected.length && <details><summary className="cursor-pointer rounded focus-visible:outline-brand">{t('ui.memory.preview.selected')}</summary>
              <ul className="mt-2 space-y-3 text-muted">{layer.recall.selected.map(item => <Reference key={item.id} item={item} />)}</ul></details>}
            {!!layer.recall.skipped_budget.length && <details><summary className="cursor-pointer rounded focus-visible:outline-brand">{t('ui.memory.preview.skipped')}</summary>
              <ul className="mt-2 space-y-3 text-muted">{layer.recall.skipped_budget.map(item => <Reference key={item.id} item={item} />)}</ul></details>}
            {!!layer.recall.suppressed.length && <details><summary className="cursor-pointer rounded focus-visible:outline-brand">{t('ui.memory.preview.covered')}</summary>
              <ul className="mt-2 space-y-2 text-muted">{layer.recall.suppressed.map(item => <li key={item.id} className="break-all font-mono">{item.id} · {t('ui.memory.revision', {revision: item.revision})} → {item.summary_id} · {t('ui.memory.revision', {revision: item.summary_revision})}</li>)}</ul></details>}
          </>}
        </section>)}
      </>}
    </section>}
  </div>
}
