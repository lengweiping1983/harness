import { LearnHarnessButton } from '@/components/ui/LearnHarnessButton'
import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale , getLocale } from '@/i18n'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowLeft, ChevronRight, Download } from 'lucide-react'
import { RefreshButton, ShowMoreControl, Tooltip } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import { requestDifferenceCopy, REQUEST_ROLE, REQUEST_TERMINAL_REASON, type RequestTraceDetail, type RequestTracePart } from '@/lib/requestTraces'
import { cn, formatDuration } from '@/lib/utils'
import { cacheMetrics } from '@/lib/promptCache'
import { watchRequestStatus } from '@/lib/watchRequestStatus'
import { JsonSource } from '@/components/ui/JsonSource'
import { RequestNumberBadge } from './RequestNumberBadge'
import { TraceStatusBadge } from './TraceStatusBadge'

export { ExecutionTracePanel as RequestTracePanel } from './ExecutionTracePanel'

export function TraceDetail({ sessionId, traceId, requestNumber, onBack, onSelect, active = true }: { sessionId: string; traceId: string; requestNumber?: number; active?: boolean; onBack: () => void; onSelect: (id: string) => void }) {
  useLocale()

  const [loaded, setLoaded] = useState<{ sessionId: string; detail: RequestTraceDetail } | null>(null)
  const data = loaded?.sessionId === sessionId && loaded.detail.request.id === traceId ? loaded.detail : null
  const [retry, setRetry] = useState(0)
  const [error, setError] = useState<ResourceText>('')
  const [part, setPart] = useState<{ part: string; index: number; label: string }>({ part: 'body', index: 0, label: t("ui.components.inspector.RequestTracePanel.b167dc5966cb") })
  const [statusError, setStatusError] = useState<ResourceText>('')
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false)
  const [diagnosticsError, setDiagnosticsError] = useState<ResourceText>('')
  const [inputShown, setInputShown] = useState(10)
  const watcher = useRef<ReturnType<typeof watchRequestStatus>>()
  useEffect(() => {
    setPart({ part: 'body', index: 0, label: t('ui.components.inspector.RequestTracePanel.b167dc5966cb') })
    setError('')
    setStatusError('')
    setInputShown(10)
  }, [sessionId, traceId])
  useEffect(() => {
    if (!active) return
    const abort = new AbortController()
    setError('')
    setDiagnosticsError(''); setDiagnosticsLoading(true)
    void api.requestTrace(sessionId, traceId, abort.signal, true).then(async (result) => {
      if (abort.signal.aborted) return
      if (!result.ok) {setError(result.copy ?? result.message); setDiagnosticsLoading(false); return}
      if (result.data.request.id !== traceId) {setError({key:'ui.http.invalidResponse'}); setDiagnosticsLoading(false); return}
      setLoaded({ sessionId, detail: result.data })
      const diagnostics = await api.requestTrace(sessionId, traceId, abort.signal)
      if (abort.signal.aborted) return
      setDiagnosticsLoading(false)
      if (diagnostics.ok && diagnostics.data.request.id === traceId) setLoaded(current => {
        if (current?.sessionId !== sessionId || current.detail.request.id !== traceId) return current
        // 诊断较慢，保留已由状态观察确认的终态元数据。
        return {sessionId, detail: {...diagnostics.data, request: current.detail.request}}
      })
      else setDiagnosticsError(diagnostics.ok ? {key:'ui.http.invalidResponse'} : diagnostics.copy ?? diagnostics.message)
    })
    return () => abort.abort()
  }, [sessionId, traceId, active, retry])
  const status = data?.request.status
  useEffect(() => {
    if (!status || !active) return
    const watch = watchRequestStatus((signal) => api.requestTraceStatus(sessionId, traceId, signal),
      (row) => setLoaded((current) => current?.sessionId === sessionId && current.detail.request.id === traceId ? { ...current, detail: { ...current.detail, request: row } } : current), setStatusError, status)
    watcher.current = watch
    return () => { watcher.current = undefined; watch.dispose() }
  }, [sessionId, traceId, status, active])
  const selectedMessage = data?.messages[part.index]
  const partLabel = part.part === 'body' ? t('ui.components.inspector.RequestTracePanel.b167dc5966cb')
    : part.part === 'tools' ? t('ui.components.inspector.RequestTracePanel.2433800bfcbe')
      : part.part === 'message' && selectedMessage ? `${part.index + 1}. ${REQUEST_ROLE[selectedMessage.role] ?? selectedMessage.role}` : part.label
  const largestInputs = data?.composition.largest_inputs ?? []
  const inputVisibleCount = largestInputs.length < 20 ? largestInputs.length : Math.min(inputShown, largestInputs.length)
  const toolDefinitions = data?.tool_definitions ?? data?.tool_names?.map((name, index) => ({ index, name, description: '' })) ?? []
  const showRequestBody = () => setPart({ part: 'body', index: 0, label: t('ui.components.inspector.RequestTracePanel.b167dc5966cb') })
  const bodyLinkMarker = '\uE000'
  const checkpointNote = t('ui.components.inspector.RequestTracePanel.f97ffa55d457', { requestBody: bodyLinkMarker }).split(bodyLinkMarker)
  return <section className="request-trace-detail space-y-3" aria-label={t("ui.components.inspector.RequestTracePanel.0062dae85c67")}>
    <LearnHarnessButton topic="request" />
    <div className="flex items-center justify-between gap-2">
      <button type="button" onClick={onBack} className="flex items-center gap-1 text-xs text-muted"><ArrowLeft className="h-3.5 w-3.5" />{t("ui.components.inspector.RequestTracePanel.1947d487a2c8")}</button>
      <a href={api.requestTraceExport(sessionId, traceId)} download className="flex items-center gap-1 text-xs text-brand"><Download className="h-3.5 w-3.5" />{t("ui.components.inspector.RequestTracePanel.7089690ca993")}</a>
    </div>
    {error ? <div className="text-xs"><p role="alert" className="text-danger">{noticeText(error)}</p><button type="button" className="mt-1 text-brand underline" onClick={() => setRetry(value => value + 1)}>{t('ui.requestComposition.retry')}</button></div> : null}
    {data && diagnosticsLoading ? <p role="status" className="text-xs text-muted">{t('ui.requestComposition.analyzing')}</p> : null}
    {data && diagnosticsError ? <div className="text-xs"><p role="alert" className="text-danger">{noticeText(diagnosticsError)}</p><button type="button" className="mt-1 text-brand underline" onClick={() => setRetry(value => value + 1)}>{t('ui.requestComposition.retry')}</button></div> : null}
    {data ? <div className="request-trace-columns">
      <div className="min-w-0 space-y-3">
      <h3 className="flex items-center gap-2 ui-section-title">{requestNumber !== undefined ? <RequestNumberBadge number={requestNumber} /> : null}<span className="min-w-0 break-all">{data.request.model}</span></h3>
      <p className="break-all text-2xs leading-relaxed text-faint">{data.request.endpoint}<br />{data.request.session_id}</p>
      <details className="request-trace-disclosure text-xs text-muted">
        <summary className="request-trace-disclosure-heading"><ChevronRight aria-hidden className="h-3.5 w-3.5 shrink-0" />{t('ui.requestComposition.title')}</summary>
        <div className="request-trace-disclosure-content">
        {data.request.body_redacted ? <p className="mb-2 text-2xs text-warn">{t('ui.requestComposition.redacted')}</p> : null}
        <p className="mb-2 tabular-nums">{t('ui.requestComposition.totals', { wire: data.request.body_bytes.toLocaleString(getLocale()), archive: data.composition.normalized_bytes.toLocaleString(getLocale()) })}</p>
        <dl className="space-y-1">{data.composition.groups.map(row => <div key={row.kind} className="flex justify-between gap-2"><dt>{t(`ui.requestComposition.${row.kind}`)}</dt><dd className="tabular-nums">{row.bytes.toLocaleString(getLocale())} B</dd></div>)}</dl>
        <h4 className="mb-1 mt-3 font-medium text-ink">{t('ui.requestComposition.largestInputs')}</h4>
        <ul className="space-y-1">{largestInputs.slice(0, inputVisibleCount).map(row => <li key={row.index}><button type="button" className="flex w-full justify-between gap-2 rounded text-left text-brand hover:underline focus-visible:outline focus-visible:outline-brand" onClick={() => setPart({ part: 'message', index: row.index, label: `${row.index + 1}. ${REQUEST_ROLE[row.role] ?? row.role}` })}><span className="min-w-0 break-all">{row.index + 1}. {REQUEST_ROLE[row.role] ?? row.role} · {row.path}</span><span className="shrink-0 tabular-nums">{row.bytes.toLocaleString(getLocale())} B</span></button></li>)}</ul>
        {largestInputs.length >= 20 && inputVisibleCount < largestInputs.length ? <ShowMoreControl onClick={() => setInputShown(value => Math.min(value + 10, largestInputs.length))} /> : null}
        <h4 className="mb-1 mt-3 font-medium text-ink">{t('ui.requestComposition.largestTools')}</h4>
        <p className="mb-1 text-2xs leading-relaxed text-faint">{t('ui.requestComposition.largestToolsNote')}</p>
        <ul className="space-y-1">{data.composition.largest_tools.map(row => <li key={row.index}><ToolDescription description={row.description}><button type="button" className="flex w-full justify-between gap-2 rounded text-left text-brand hover:underline focus-visible:outline focus-visible:outline-brand" onClick={() => setPart({ part: 'tool', index: row.index, label: row.name })}><span className="min-w-0"><span data-tool-name className="block break-all">{row.name}</span></span><span className="shrink-0 tabular-nums">{row.bytes.toLocaleString(getLocale())} B</span></button></ToolDescription></li>)}</ul>
        {!data.composition.largest_inputs.length && !data.composition.largest_tools.length ? <p className="mt-2 text-2xs text-faint">{t('ui.requestComposition.empty')}</p> : null}
        </div>
      </details>
      <section aria-label={t("ui.components.inspector.RequestTracePanel.d173187ba004")} className="refresh-action-container space-y-1.5 rounded-lg border border-line p-3 text-xs text-muted">
        <div className="flex items-center justify-between gap-2">
          <h4 className="font-medium text-ink">{t("ui.components.inspector.RequestTracePanel.d173187ba004")}</h4>
          <RefreshButton size="xs" label={t("ui.components.inspector.RequestTracePanel.1764e5a7307d")} onClick={() => watcher.current?.refresh()} />
        </div>
        {status === 'started' ? <p className="text-2xs text-faint">{t("ui.components.inspector.RequestTracePanel.f334e037e374")}</p> : null}
        {statusError ? <p role="alert" className="text-danger">{noticeText(statusError)}</p> : null}
        <div className="flex flex-wrap items-center gap-2"><TraceStatusBadge status={data.request.status} />{data.request.observation ? <span>{REQUEST_TERMINAL_REASON[data.request.observation.terminal_reason] ?? data.request.observation.terminal_reason}</span> : null}</div>
        {data.request.observation ? <><p>{t("ui.requestStats.stream", {duration: formatDuration(data.request.observation.duration_ms)})}</p>
          <p>{t("ui.requestStats.events", {count: data.request.observation.event_count.toLocaleString(getLocale())})}</p>
          <p>{t("ui.requestStats.firstEvent", {duration: data.request.observation.first_event_ms === null ? t("ui.components.inspector.RequestTracePanel.172346f1c1fd") : formatDuration(data.request.observation.first_event_ms)})}</p>
          {data.request.observation.text_diagnostic ? <details className="text-2xs"><summary className="cursor-pointer">{t('ui.context.diagnostic')}</summary><JsonSource text={JSON.stringify(data.request.observation.text_diagnostic, null, 2) ?? 'null'} className="mt-2 max-h-64" active={active} /></details> : null}
          {data.request.observation.error_type ? <p className="break-all font-mono text-2xs text-danger">{t("ui.requestStats.errorType", {type: data.request.observation.error_type})}</p> : null}
        </> : <p className="text-2xs text-faint">{data.request.status === 'started' ? t("ui.components.inspector.RequestTracePanel.2d9b369ecb97") : t("ui.components.inspector.RequestTracePanel.50d5cda2654c")}</p>}
        <p className="text-2xs text-faint">{t("ui.components.inspector.RequestTracePanel.3ddc281a4442")}</p>
      </section>
      {data.request.context_checkpoint ? <details className="request-trace-disclosure text-2xs text-muted"><summary className="request-trace-disclosure-heading"><ChevronRight aria-hidden className="h-3.5 w-3.5 shrink-0" />{t("ui.components.inspector.RequestTracePanel.431e3d850cf5")}</summary><div className="request-trace-disclosure-content">
        <p className="mt-2">{t("ui.requestStats.checkpoint", {events: data.request.context_checkpoint.event_count.toLocaleString(getLocale()), nodes: data.request.context_checkpoint.surface_nodes.toLocaleString(getLocale())})}</p>
        <p className="mt-2 text-faint">{checkpointNote[0]}<button type="button" className="rounded text-brand underline hover:text-brand/80 focus-visible:outline focus-visible:outline-brand" onClick={showRequestBody}>{t("ui.components.inspector.RequestTracePanel.b167dc5966cb")}</button>{checkpointNote[1]}</p></div>
      </details> : <p className="text-2xs text-faint">{t("ui.components.inspector.RequestTracePanel.19aaaa696226")}</p>}
      <section aria-label={t("ui.components.inspector.RequestTracePanel.a9635c87926c")} className="space-y-2 rounded-lg border border-line p-3 text-xs text-muted">
        <h4 className="font-medium text-ink">{t("ui.components.inspector.RequestTracePanel.a9635c87926c")}</h4>
        <p>{cacheMetrics(data.request.usage).hitRate === null ? t("ui.components.inspector.RequestTracePanel.e2d8fd906df6") : t("ui.components.inspector.RequestTracePanel.6af5574b6d2f", {p0: ((data.request.usage?.cache_read_tokens ?? 0).toLocaleString(getLocale())), p1: ((data.request.usage?.input_tokens ?? 0).toLocaleString(getLocale())), p2: ((cacheMetrics(data.request.usage).hitRate! * 100).toFixed(1))})}</p>
        {data.cache_comparison ? <>
          <p>{t("ui.requestStats.cachePrefix", {messages: data.cache_comparison.shared_messages.toLocaleString(getLocale()), characters: data.cache_comparison.shared_prefix_chars.toLocaleString(getLocale())})}</p>
          <button type="button" className="text-xs text-brand underline" onClick={() => onSelect(data.cache_comparison!.previous_id)}>{t("ui.components.inspector.RequestTracePanel.fd6c94d9d4f3")}</button>
          {data.cache_comparison.first_changed_message !== null ? <p>{t("ui.requestStats.firstChange", {index: data.cache_comparison.first_changed_message + 1})}</p> : null}
          {data.cache_comparison.sequence_change ? <p>{{ modified: t("ui.components.inspector.RequestTracePanel.f691fb361fcc"), appended: t("ui.components.inspector.RequestTracePanel.8d77b6c2269f"), truncated: t("ui.components.inspector.RequestTracePanel.2ed868d39313"), identical: t("ui.components.inspector.RequestTracePanel.d23c648e7c9e") }[data.cache_comparison.sequence_change]}</p> : null}
          {data.cache_comparison.first_difference ? <p className="break-all font-mono text-2xs">{noticeText(requestDifferenceCopy(data.cache_comparison.first_difference))}</p> : null}
          {data.cache_comparison.first_changed_message !== null && data.cache_comparison.first_changed_message < data.messages.length ? <button type="button" className="text-xs text-brand underline" onClick={() => { const index = data.cache_comparison!.first_changed_message!; setPart({ part: 'message', index, label: `${index + 1}. ${REQUEST_ROLE[data.messages[index].role] ?? data.messages[index].role}` }) }}>{t("ui.components.inspector.RequestTracePanel.639359df02e3")}</button> : null}
          {data.cache_comparison.changes.map((change) => <p key={change} className="text-warn">{t({endpoint: 'ui.requestStats.cacheChange.endpoint', model: 'ui.requestStats.cacheChange.model', tools: 'ui.requestStats.cacheChange.tools', settings: 'ui.requestStats.cacheChange.settings'}[change], {settings: data.cache_comparison!.changed_settings.join(t('ui.common.list_separator'))})}</p>)}
          <p className="text-2xs text-faint">{t("ui.components.inspector.RequestTracePanel.59e2aa3d183c")}</p>
        </> : <p className="text-2xs text-faint">{t("ui.components.inspector.RequestTracePanel.3f08bac1f181")}</p>}
      </section>
      <details className="request-trace-disclosure text-xs text-muted"><summary className="request-trace-disclosure-heading"><ChevronRight aria-hidden className="h-3.5 w-3.5 shrink-0" />{t("ui.components.inspector.RequestTracePanel.9541ee3d4b15")}</summary><div className="request-trace-disclosure-content"><JsonSource text={JSON.stringify(data.parameters, null, 2) ?? 'null'} active={active} /></div></details>
      <div className="request-trace-disclosure"><button type="button" onClick={showRequestBody} className="request-trace-disclosure-heading w-full text-xs text-brand"><ChevronRight aria-hidden className="h-3.5 w-3.5 shrink-0" />{t("ui.components.inspector.RequestTracePanel.649934228541")}</button></div>
      <details className="request-trace-disclosure text-xs text-muted"><summary className="request-trace-disclosure-heading"><ChevronRight aria-hidden className="h-3.5 w-3.5 shrink-0" />{t("ui.requestStats.tools", {count: data.tool_names.length.toLocaleString(getLocale())})}</summary><div className="request-trace-disclosure-content space-y-2">
        <button type="button" onClick={() => setPart({ part: 'tools', index: 0, label: t("ui.components.inspector.RequestTracePanel.2433800bfcbe") })} className="text-brand hover:underline focus-visible:outline focus-visible:outline-brand">{t('ui.requestStats.viewAllTools')}</button>
        <ul className="space-y-1">{toolDefinitions.map(tool => <li key={`${tool.index}:${tool.name}`}><ToolDescription description={tool.description}><button type="button" onClick={() => setPart({ part: 'tool', index: tool.index, label: tool.name })} className="max-w-full text-left text-brand hover:underline focus-visible:outline focus-visible:outline-brand"><span data-tool-name className="block break-all">{tool.name}</span></button></ToolDescription></li>)}</ul>
      </div></details>
      <details className="request-trace-disclosure text-xs text-muted"><summary className="request-trace-disclosure-heading"><ChevronRight aria-hidden className="h-3.5 w-3.5 shrink-0" />{t("ui.requestStats.messages", {count: data.messages.length.toLocaleString(getLocale())})}</summary><div className="request-trace-disclosure-content">
      <ol className="space-y-2">
        {data.messages.map((message) => <li key={message.index}><button type="button" aria-pressed={part.part === 'message' && part.index === message.index} onClick={() => setPart({ part: 'message', index: message.index, label: `${message.index + 1}. ${REQUEST_ROLE[message.role] ?? message.role}` })} className={cn('w-full rounded-lg border px-3 py-2.5 text-left transition-colors hover:border-brand/50 hover:bg-raised focus-visible:outline focus-visible:outline-brand', part.part === 'message' && part.index === message.index ? 'border-brand/60 bg-brand/5' : 'border-line bg-canvas')}>
          <div className="flex items-center gap-2 text-xs"><span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-raised text-2xs font-semibold tabular-nums text-muted">{message.index + 1}</span><span data-message-role={message.role} className="rounded-md bg-brand/10 px-2 py-0.5 text-2xs font-medium text-brand">{REQUEST_ROLE[message.role] ?? message.role}</span><span className="ml-auto shrink-0 text-2xs text-faint">{t("ui.metrics.characters", {count: message.chars.toLocaleString(getLocale())})}</span></div>
          <p className="mt-1 line-clamp-2 break-all text-2xs leading-relaxed text-faint">{message.preview || t("ui.components.inspector.RequestTracePanel.3ed8a1c06830")}</p>
          {message.tool_sources?.map(source => <p key={`${source.call_id}:${source.kind}`} className="mt-1 break-all font-mono text-2xs text-faint">{t('ui.requestSources.tool', { tool: source.tool, call: source.call_id, event: source.event_seq, origin: source.call_seq })}</p>)}
          {message.prompt_sources?.length ? <p className="mt-1 text-2xs text-faint">{t('ui.requestSources.promptCount', { shown: message.prompt_sources.length, total: message.prompt_source_count ?? message.prompt_sources.length })}</p> : null}
          {message.prompt_sources?.map(source => <p key={`${source.part}:${source.source}`} className="mt-1 break-all font-mono text-2xs text-faint">{t('ui.requestSources.prompt', { source: source.source, part: source.part, event: source.event_seq, bytes: source.bytes.toLocaleString(getLocale()) })}</p>)}
        </button></li>)}
      </ol></div></details>
      </div>
      <div className="request-trace-reader min-w-0">
        <TracePart key={`${sessionId}:${traceId}:${part.part}:${part.index}`} sessionId={sessionId} traceId={traceId} active={active} {...part} label={partLabel} />
      </div>
    </div> : !error ? <p className="text-xs text-muted">{t("ui.components.inspector.RequestTracePanel.c9b861a343b4")}</p> : null}
  </section>
}

export function ToolDescription({ description, children, focusable = false, onTrigger }: { description?: string; children: ReactNode; focusable?: boolean; onTrigger?: () => void }) {
  return description || onTrigger
    ? <Tooltip side="right" delay={180} anchorClassName="block w-full" anchorSelector="[data-tool-name]" anchorTabIndex={focusable ? 0 : undefined} onTrigger={onTrigger} content={description ? <span className="whitespace-pre-wrap break-words">{description}</span> : null}>{children}</Tooltip>
    : children
}

function TracePart({ sessionId, traceId, part, index, label, active }: { sessionId: string; traceId: string; part: string; index: number; label: string; active: boolean }) {
  useLocale()

  const [data, setData] = useState<RequestTracePart | null>(null)
  const [offset, setOffset] = useState(0)
  const [raw, setRaw] = useState(false)
  const readPart = part === 'body' && !raw ? 'body_formatted' : part
  const [error, setError] = useState<ResourceText>('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!active) return
    const abort = new AbortController()
    setError('')
    setData(null)
    void api.requestTracePart(sessionId, traceId, readPart, index, offset, abort.signal).then((result) => {
      if (abort.signal.aborted) return
      if (result.ok) setData(result.data)
      else setError(result.copy ?? result.message)
    })
    return () => abort.abort()
  }, [sessionId, traceId, readPart, index, offset, active, retry])
  return <section className="rounded-lg border border-line bg-raised/40 p-3" aria-label={label}>
    <h4 className="mb-2 text-xs font-medium text-ink">{part === 'body' ? <button type="button" aria-pressed={raw} title={raw ? t("ui.components.inspector.RequestTracePanel.b69d9bda23f7") : t("ui.components.inspector.RequestTracePanel.5aabc99eeed5")} onClick={() => { setRaw((value) => !value); setOffset(0); setData(null) }} className="flex w-full items-center justify-between gap-2 rounded text-left hover:text-brand focus-visible:outline focus-visible:outline-brand">
      <span>{label}</span><span className="text-2xs text-faint">{raw ? t("ui.components.inspector.RequestTracePanel.953dc6ff4a0c") : t("ui.components.inspector.RequestTracePanel.e70725432689")}</span>
    </button> : label}</h4>
    {error ? <div className="text-xs"><p role="alert" className="text-danger">{noticeText(error)}</p><button type="button" className="mt-1 text-brand underline" onClick={() => setRetry(value => value + 1)}>{t('ui.requestComposition.retry')}</button></div> : null}
    {data ? <>
      <JsonSource text={data.text} active={active} className="max-h-detail" />
      <div className="mt-2 flex items-center justify-between gap-2 text-2xs text-faint">
        <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 24000))} className="disabled:opacity-40">{t("ui.components.inspector.RequestTracePanel.5c73eb92d825")}</button>
        <span>{data.text ? data.offset + 1 : data.offset}–{data.offset + Array.from(data.text).length} / {data.total_chars}</span>
        <button type="button" disabled={data.next_offset === null} onClick={() => setOffset(data.next_offset!)} className="disabled:opacity-40">{t("ui.components.inspector.RequestTracePanel.4cd4dc1f03d6")}</button>
      </div>
    </> : !error ? <p className="text-xs text-muted">{t("ui.components.inspector.RequestTracePanel.4c66c1a9e606")}</p> : null}
  </section>
}
