import {useAppStore} from '@/store/app'
import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { CompactionProgress } from '@/components/ui/CompactionProgress'
import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ChevronRight, Layers } from 'lucide-react'
import { RefreshButton, ShowMoreControl } from '@/components/ui/primitives'
import { useChatStore } from '@/store/chat'
import { api } from '@/lib/api'
import { COMPACTION_STATUS, CONTEXT_ROLE, mergeCompactions, type ContextAudit } from '@/lib/contextAudit'
import type { RequestTracePart } from '@/lib/requestTraces'
import { cn, formatClock, formatTokens } from '@/lib/utils'
import { ContextSettingsPanel } from '../overlays/ContextSettingsPanel'
import { ContextCompactControl } from './ContextCompactControl'
import { RequestTracePanel } from './RequestTracePanel'
import { SkillAuditPanel } from './SkillAuditPanel'
import { CompactionCapacityEvidence } from './CompactionCapacityEvidence'
import { ContextStatisticsPanel } from './ContextStatisticsPanel'
import { ToolResultProjectionStats } from './ToolResultProjectionStats'
import { REQUEST_STATUS } from '@/lib/requestTraces'

export function ContextAuditPanel({ sessionId, agentId, active = true }: { sessionId: string; agentId?: string; active?: boolean }) {
  useLocale()

  const modelContextWindow = useAppStore(state => {
    const model = state.sessions.find(session => session.id === sessionId)?.model || state.defaultModel
    return model ? state.models.find(entry => entry.id === model)?.context_window ?? null : undefined
  })
  const revision = useChatStore(state => active ? state.sessions[sessionId]?.contextRevision ?? 0 : 0)
  const observedRevision = useRef(revision)
  const [hasUpdates, setHasUpdates] = useState(false)
  const scope = JSON.stringify([sessionId, agentId])
  const [snapshot, setSnapshot] = useState<{ scope: string; data: ContextAudit } | null>(null)
  const data = snapshot?.scope === scope ? snapshot.data : null
  const [auditOpen, setAuditOpen] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [loading, setLoading] = useState(false)
  const [traceSelection, setTraceSelection] = useState<{ scope: string; id: string } | null>(null)
  const trace = traceSelection?.scope === scope ? traceSelection.id : null
  const setTrace = (id: string | null) => setTraceSelection(id ? { scope, id } : null)
  const [selected, setSelected] = useState<{ id: number; through: number; part: string } | null>(null)
  const request = useRef(0)
  const controller = useRef<AbortController>()
  async function load(older = false) {
    if (!active) return
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    const ticket = ++request.current
    setLoading(true)
    if (!older) setHasUpdates(false)
    setError('')
    const result = await api.contextAudit(sessionId, older ? data?.through : undefined, older ? data?.next_before ?? undefined : undefined, abort.signal, agentId)
    if (ticket !== request.current || abort.signal.aborted) return
    setLoading(false)
    if (!result.ok) { setError(result.copy ?? result.message); return }
    setSnapshot(previous => {
      const current = previous?.scope === scope ? previous.data : null
      return { scope, data: older && current ? { ...result.data, current: current.current,
        compactions: mergeCompactions(current.compactions, result.data.compactions) } : result.data }
    })
  }
  useEffect(() => {
    setSnapshot(null); setError(''); setTrace(null); setSelected(null); setHasUpdates(false)
    return () => { ++request.current; controller.current?.abort() }
  }, [sessionId, agentId])
  useEffect(() => {
    observedRevision.current = revision
    if (active) void load()
    else { ++request.current; controller.current?.abort(); setLoading(false) }
    return () => { ++request.current; controller.current?.abort() }
  }, [sessionId, agentId, active])
  useEffect(() => {
    if (observedRevision.current === revision) return
    observedRevision.current = revision
    if (!active) return
    // 连续运行事件不能反复取消尚未返回的首屏读取；保留快照并提示新记录。
    if (loading || selected || trace || data?.has_more) setHasUpdates(true)
    else void load()
  }, [revision, active])
  if (trace) return <div className="space-y-3">{hasUpdates ? <p role="status" className="text-xs text-brand">{t('ui.context.newRecords')}</p> : null}<button className="flex items-center gap-1 text-xs text-muted" onClick={() => setTrace(null)}><ArrowLeft className="h-3 w-3" />{t("ui.components.inspector.ContextAuditPanel.735d8ec4bd86")}</button><RequestTracePanel sessionId={sessionId} initialTraceId={trace} agentSessionId={agentId ? data?.session_id : undefined} active={active} /></div>
  const current = data?.current
  const latest = current?.latest_request
  const reported = current?.latest_reported_request
  const latestInput = latest?.usage?.input_tokens
  return <section aria-label={t('ui.context.review.title')} className="refresh-action-container space-y-4">
    <header className="flex items-center justify-between gap-2"><h3 className="flex items-center gap-1.5 text-sm font-semibold"><Layers className="h-3.5 w-3.5 text-brand" />{t('ui.context.review.title')}</h3>
      <RefreshButton size="xs" variant="ghost" label={t("ui.components.inspector.ContextAuditPanel.920b5628b33a")} disabled={loading} spinning={loading} onClick={() => { setSelected(null); void load() }} /></header>
    {hasUpdates ? <p role="status" className="text-xs text-brand">{t('ui.context.newRecords')}</p> : null}
    {error ? <p role="alert" className="text-xs text-danger">{noticeText(error)}</p> : null}
    {loading && !data ? <p className="text-xs text-faint">{t("ui.components.inspector.ContextAuditPanel.e17120b5396e")}</p> : null}
    {current ? <ContextStatisticsPanel statistics={current.statistics} /> : null}
    {active && !agentId ? <ContextCompactControl key={sessionId} sessionId={sessionId} onComplete={() => { if (selected || trace) setHasUpdates(true); else void load() }} /> : null}
    <details open={auditOpen} onToggle={event => setAuditOpen(event.currentTarget.open)} className="space-y-3 rounded-lg border border-line p-3"><summary className="cursor-pointer text-xs font-medium">{t('ui.context.review.records')}</summary>
    {active && auditOpen ? <>
    {reported ? <p className="text-xs text-muted">{t('ui.context.audit.reportedUsage', {tokens: formatTokens(reported.usage?.input_tokens)})}</p> : null}
    <ToolResultProjectionStats value={current?.tool_result_projection} />
    {current ? <div>      <dl className="space-y-1.5 text-2xs">{Object.entries(current.roles).map(([role, count]) => <div key={role} className="flex justify-between gap-2"><dt className="text-muted">{CONTEXT_ROLE[role] ?? role}</dt><dd className="tabular-nums">{t('ui.context.audit.items', {count: formatTokens(count)})}</dd></div>)}</dl>
      <p className="text-2xs text-faint">{t('ui.context.statistics.messages', { count: formatTokens(current.message_count) })}</p>
      <p className="border-t border-line pt-2 text-2xs text-faint">{t('ui.context.audit.snapshot', {events: formatTokens(current.event_count), nodes: formatTokens(current.surface_nodes)})}</p>
</div> : null}
    {data ? <SkillAuditPanel key={`${sessionId}:${agentId}:${data.through}`} sessionId={sessionId} agentId={agentId} through={data.through} /> : null}
    {latest ? <button type="button" onClick={() => setTrace(latest.id)} className="w-full rounded-lg border border-line p-3 text-left hover:bg-raised">
      <span className="flex items-center justify-between text-xs font-medium">{t(agentId ? 'ui.context.audit.latestChildRequest' : 'ui.context.audit.latestMainRequest')}<ChevronRight className="h-3 w-3" /></span>
      <span className="mt-1 block truncate text-2xs text-muted">{t('ui.context.audit.requestMeta', {model: latest.model, time: formatClock(latest.time)})}</span>
      <span className="mt-1 block text-2xs text-muted">{REQUEST_STATUS[latest.status] ?? latest.status}</span>
      <span className="mt-1 block text-2xs text-faint">{t(typeof latestInput === 'number' && Number.isSafeInteger(latestInput) && latestInput >= 0 ? 'ui.context.audit.requestUsage' : 'ui.context.audit.requestUnreported', {tokens: formatTokens(latestInput), messages: formatTokens(latest.message_count), tools: formatTokens(latest.tool_count)})}</span>
    </button> : null}
    {reported && reported.id !== latest?.id ? <button type="button" onClick={() => setTrace(reported.id)} className="w-full rounded-lg border border-line p-3 text-left text-2xs hover:bg-raised">
      <span className="block font-medium">{t('ui.context.statistics.latestReported')}</span>
      <span className="mt-1 block text-muted">{t('ui.context.audit.reportedMeta', {model: reported.model, time: formatClock(reported.time), tokens: formatTokens(reported.usage?.input_tokens)})}</span>
      <span className="mt-1 block text-faint">{REQUEST_STATUS[reported.status] ?? reported.status}</span>
    </button> : null}
    <details className="space-y-2 rounded-lg border border-line p-3"><summary className="cursor-pointer text-xs font-medium"><span>{t("ui.components.inspector.ContextAuditPanel.bc30ea799c52")}</span><span className="ml-2 text-2xs font-normal text-faint">{t('ui.context.audit.loaded', {count: data?.compactions.length ?? 0})}</span></summary><p className="text-2xs leading-relaxed text-faint">{t("ui.components.inspector.ContextAuditPanel.3754f41ede6e")}</p>
      {data?.has_more ? <ShowMoreControl disabled={loading} onClick={() => void load(true)} /> : null}
      {data && !data.compactions.length ? <p className="py-2 text-xs text-faint">{t("ui.components.inspector.ContextAuditPanel.c531fdf6694e")}</p> : null}
      <ol className="space-y-2">{data?.compactions.map((row) => <li key={row.id} className="rounded-lg border border-line p-2.5 text-2xs">
        <div className="flex items-center justify-between gap-2"><span className={cn('font-medium', row.status === 'failed' && 'text-danger')}>{COMPACTION_STATUS[row.status]}</span><time className="text-faint">{formatClock(row.time)}</time></div>
        <p className="mt-1 text-faint">{t(row.turn ? row.policy_version ? 'ui.context.audit.identityTurnPolicy' : 'ui.context.audit.identityTurn' : row.policy_version ? 'ui.context.audit.identityPolicy' : 'ui.context.audit.identity', {id: row.id, turn: row.turn ?? 0, policy: row.policy_version ?? 0})}</p>
        {row.before_approx_tokens !== undefined ? <p className="mt-1 tabular-nums text-muted">{t(row.after_approx_tokens !== undefined ? 'ui.context.audit.estimatedChange' : 'ui.context.audit.estimated', {before: formatTokens(row.before_approx_tokens), after: formatTokens(row.after_approx_tokens)})}</p> : null}
        <CompactionCapacityEvidence row={row} />
        {row.status === 'running' ? <div className="mt-1"><CompactionProgress completed={row.completed_chunks} total={row.total_chunks}
          stage={row.stage} attempt={row.attempt} maxAttempts={row.max_attempts} retryInSeconds={row.retry_in_seconds} /></div> : null}
        {row.status !== 'running' && row.total_chunks ? <p className="mt-1 tabular-nums text-muted">{t('ui.context.audit.chunks', {completed: row.completed_chunks ?? 0, total: row.total_chunks})}</p> : null}
        {row.shadowed_count !== undefined ? <p className="mt-1 text-faint">{t(row.retained_nodes !== undefined ? 'ui.context.audit.replacedRetained' : 'ui.context.audit.replaced', {count: row.shadowed_count, retained: row.retained_nodes ?? 0})}</p> : null}
        {row.pruned_results > 0 ? <p className="mt-1 text-faint">{t('ui.context.audit.pruned', {count: row.pruned_results, chars: formatTokens(row.chars_removed)})}</p> : null}
        {row.error ? <p className="mt-1 break-words text-danger">{row.error}</p> : null}
        {row.summary_preview ? <p className="mt-2 line-clamp-3 whitespace-pre-wrap break-words text-muted">{row.summary_preview}</p> : null}
        {row.summary_seq !== undefined || row.error ? <div className="mt-2 flex gap-3">{(row.summary_seq !== undefined ? [['summary', t("ui.components.inspector.ContextAuditPanel.bea0336e20d1")], ['sources', t("ui.components.inspector.ContextAuditPanel.5cf30462c28f")]] : [['summary', t("ui.components.inspector.ContextAuditPanel.8cb39edbbbc6")]]).map(([part, label]) => <button key={part} className="text-brand hover:underline" onClick={() => setSelected({ id: row.id, through: data.through, part: part! })}>{label}</button>)}</div> : null}
        {selected?.id === row.id ? <CompactionReader key={`${selected.id}:${selected.through}:${selected.part}`} sessionId={sessionId} agentId={agentId} {...selected} onClose={() => setSelected(null)} /> : null}
      </li>)}</ol>
    </details>
    </> : null}</details>
    {active && !agentId ? <details className="rounded-lg border border-line p-3" data-context-settings><summary className="cursor-pointer text-xs font-medium">{t('ui.context.review.settings')}</summary><div className="mt-3"><ContextSettingsPanel key={sessionId} modelContextWindow={modelContextWindow} /></div></details> : null}
  </section>
}

function CompactionReader({ sessionId, agentId, id, through, part, onClose }: { sessionId: string; agentId?: string; id: number; through: number; part: string; onClose: () => void }) {
  useLocale()

  const [offset, setOffset] = useState(0)
  const [data, setData] = useState<RequestTracePart | null>(null)
  const [error, setError] = useState<ResourceText>('')
  useEffect(() => {
    const abort = new AbortController()
    setData(null)
    setError('')
    void api.compactionPart(sessionId, id, through, part, offset, abort.signal, agentId).then((result) => {
      if (abort.signal.aborted) return
      if (result.ok) setData(result.data)
      else setError(result.copy ?? result.message)
    })
    return () => abort.abort()
  }, [sessionId, agentId, id, through, part, offset])
  return <section aria-label={part === 'summary' ? t("ui.components.inspector.ContextAuditPanel.e56796f6a6f0") : t("ui.components.inspector.ContextAuditPanel.5cf30462c28f")} className="mt-3 space-y-2 border-t border-line pt-2">
    <div className="flex justify-between gap-2 text-faint"><span>{part === 'summary' ? t("ui.components.inspector.ContextAuditPanel.e56796f6a6f0") : t("ui.components.inspector.ContextAuditPanel.26fcc384cb0a")}</span><button onClick={onClose}>{t("ui.components.inspector.ContextAuditPanel.afd4b783536b")}</button></div>
    {error ? <p role="alert" className="text-danger">{noticeText(error)}</p> : data ? <><pre tabIndex={0} className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-raised p-2 font-mono text-xs leading-relaxed">{data.text}</pre>
      <div className="flex items-center justify-between gap-2 text-faint"><button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 24000))}>{t("ui.components.inspector.ContextAuditPanel.5c73eb92d825")}</button><span>{t('ui.context.audit.range', {start: formatTokens(data.total_chars ? data.offset + 1 : 0), end: formatTokens(Math.min(data.total_chars, data.offset + Array.from(data.text).length)), total: formatTokens(data.total_chars)})}</span><button disabled={data.next_offset === null} onClick={() => setOffset(data.next_offset!)}>{t("ui.components.inspector.ContextAuditPanel.4cd4dc1f03d6")}</button></div></> : <p className="text-faint">{t("ui.components.inspector.ContextAuditPanel.346ff60e6c7c")}</p>}
  </section>
}
