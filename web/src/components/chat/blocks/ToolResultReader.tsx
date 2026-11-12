import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { ToolResultPart, ToolResultReference } from '@/lib/protocol.generated'
import { t, useLocale } from '@/i18n'
import { usePageVisible } from '@/hooks/usePageVisible'
import { useAppStore } from '@/store/app'
import { Button, CopyButton } from '@/components/ui/primitives'
import { formatTokens, isProbablyJson } from '@/lib/utils'
import { JsonSource } from '@/components/ui/JsonSource'

export type ToolResultScope = { sessionId: string; agentId?: string; active?: boolean }

export function ToolResultReader({ reference, scope }: { reference: ToolResultReference; scope: ToolResultScope }) {
  useLocale()
  const visible = usePageVisible()
  const activeSessionId = useAppStore(state => state.activeSessionId)
  const enabled = visible && scope.active !== false && (!activeSessionId || activeSessionId === scope.sessionId)
  const [request, setRequest] = useState<{ offset: number; query?: string; revision: number }>({ offset: 0, revision: 0 })
  const [query, setQuery] = useState('')
  const [part, setPart] = useState<ToolResultPart | null>(null)
  const [issue, setIssue] = useState<{ code: string; message: string } | null>(null)
  const error = issue?.code === 'tool-result/changed' ? t('ui.toolResult.changed')
    : issue?.code === 'tool-result/missing' ? t('ui.toolResult.missing') : issue?.message ?? ''
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    setLoading(true); setIssue(null)
    void api.toolResult(scope.sessionId, reference, request.offset, request.query, controller.signal, scope.agentId).then(response => {
      if (controller.signal.aborted) return
      if (!response.ok) { setIssue({ code: response.code, message: response.message }); return }
      const data = response.data
      const end = data.offset + Array.from(data.text).length
      const noMatch = request.query && data.match_offset === null
      if (data.sha256 !== reference.sha256 || data.event_seq !== reference.event_seq || data.result_index !== reference.result_index
        || data.total_chars !== reference.total_chars || data.offset < 0 || end > data.total_chars
        || (!noMatch && data.next_offset !== (end < data.total_chars ? end : null))) {
        setIssue({ code: 'tool-result/changed', message: '' }); return
      }
      setPart(data)
    }).catch(failure => {
      if (!controller.signal.aborted) setIssue({ code: '', message: failure instanceof Error ? failure.message : String(failure) })
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [enabled, scope.sessionId, scope.agentId, reference.event_seq, reference.result_index, reference.sha256, reference.total_chars,
    request.offset, request.query, request.revision])
  const load = (offset: number, search?: string) => setRequest(previous => ({ offset, query: search, revision: previous.revision + 1 }))
  const chars = Array.from(part?.text ?? '')
  const highlight = request.query && part?.match_offset !== null && part?.match_offset !== undefined ? part.match_offset - part.offset : -1
  const matchLength = Array.from(request.query ?? '').length
  const disabled = loading || !enabled
  return <div className="space-y-2" aria-busy={loading}>
    <p className="text-2xs text-faint">{t('ui.toolResult.range', { start: formatTokens(part?.offset ?? 0), end: formatTokens((part?.offset ?? 0) + chars.length), total: formatTokens(reference.total_chars) })}</p>
    <form className="flex flex-wrap items-center gap-2" onSubmit={event => { event.preventDefault(); if (query.trim()) load(0, query) }}>
      <input type="search" className="min-w-0 flex-1 rounded-md border border-line bg-canvas px-2 py-1 text-xs text-ink focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand" aria-label={t('ui.toolResult.search')} value={query} maxLength={256} onChange={event => setQuery(event.target.value)} />
      <Button size="xs" type="submit" disabled={disabled || !query.trim()}>{t('ui.toolResult.search')}</Button>
      {request.query && part?.match_offset != null && <Button size="xs" disabled={disabled} onClick={() => load(part.match_offset! + matchLength, request.query)}>{t('ui.toolResult.nextMatch')}</Button>}
    </form>
    {loading && <p role="status" className="text-xs text-muted">{t('ui.toolResult.loading')}</p>}
    {error && <div role="alert" className="text-xs text-danger">{error} <Button size="xs" disabled={!enabled} onClick={() => load(request.offset, request.query)}>{t('ui.toolResult.retry')}</Button></div>}
    {!loading && !error && part && <>
      {request.query && part.match_offset === null ? <p className="text-xs text-muted">{t('ui.toolResult.noMatch')}</p> :
        !request.query && isProbablyJson(part.text) ? <JsonSource text={part.text} active={enabled} label={t('ui.toolResult.original')} /> :
          <pre tabIndex={0} aria-label={t('ui.toolResult.original')} className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md bg-raised p-3 font-mono text-xs text-muted">{highlight < 0 ? part.text : <>{chars.slice(0, highlight).join('')}<mark className="bg-brand/[0.12] text-ink">{chars.slice(highlight, highlight + matchLength).join('')}</mark>{chars.slice(highlight + matchLength).join('')}</>}</pre>}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="xs" disabled={disabled || part.offset === 0} onClick={() => load(Math.max(0, part.offset - 8192))}>{t('ui.toolResult.previous')}</Button>
        <Button size="xs" disabled={disabled || part.next_offset === null} onClick={() => load(part.next_offset!)}>{t('ui.toolResult.next')}</Button>
        <CopyButton text={part.text} label={t('ui.toolResult.copyPart')} />
      </div>
    </>}
  </div>
}
