import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { failureText } from '@/lib/resourceError'
import { listVisibleCount } from '../../lib/listPagination'
import { useEffect, useRef, useState } from 'react'
import { Bot, ChevronRight, Wrench } from 'lucide-react'
import { RefreshButton, ShowMoreControl } from '@/components/ui/primitives'
import { t, useLocale, getLocale } from '@/i18n'
import { api } from '@/lib/api'
import type { TraceEntry } from '@/lib/executionTrace'
import { REQUEST_STATUS, type RequestTracePart } from '@/lib/requestTraces'
import { cn, formatClock, formatDuration, isProbablyJson } from '@/lib/utils'
import { usePageVisible } from '@/hooks/usePageVisible'
import { ToolDescription, TraceDetail } from './RequestTracePanel'
import { RequestNumberBadge } from './RequestNumberBadge'
import { TraceStatusBadge } from './TraceStatusBadge'
import { JsonSource } from '@/components/ui/JsonSource'

type Level = { session?: string; label: string; rows: TraceEntry[]; after: string | null; more: boolean; missing: boolean; scroll: number; shown: Record<string, number>; collapsed: Record<string, boolean> }
const level = (session?: string, label = ''): Level => ({ session, label, rows: [], after: null, more: false, missing: false, scroll: 0, shown: {}, collapsed: {} })

export function ExecutionTracePanel({ sessionId, initialTraceId, agentSessionId, active = true }: { sessionId: string | null; initialTraceId?: string; agentSessionId?: string; active?: boolean }) {
  useLocale()
  const [levels, setLevels] = useState<Level[]>([level(agentSessionId)])
  const [selected, setSelected] = useState<string | null>(initialTraceId ?? null)
  const [error, setError] = useState<ResourceText>('')
  const [loading, setLoading] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const visible = usePageVisible()
  const current = levels[levels.length - 1]
  const levelsRef = useRef(levels)
  levelsRef.current = levels
  const container = useRef<HTMLElement>(null)
  const request = useRef<AbortController>()
  const busy = useRef(false)
  const scroller = () => container.current?.closest('.overflow-y-auto') ?? container.current?.parentElement
  const saveScroll = () => { if (!selected) current.scroll = scroller()?.scrollTop ?? 0 }
  useEffect(() => {
    setLevels([level(agentSessionId)])
    setSelected(initialTraceId ?? null)
  }, [sessionId, agentSessionId, initialTraceId])
  useEffect(() => {
    if (!sessionId || !active || !visible || selected) return
    let disposed = false
    const abort = new AbortController()
    request.current = abort
    async function load() {
      if (busy.current) return
      busy.current = true
      setLoading(true)
      try {
        const count = Math.max(1, Math.ceil((levelsRef.current.at(-1)?.rows.length ?? 0) / 50))
        let rows: TraceEntry[] = [], after: string | undefined, more = false, missing = false
        for (let index = 0; index < count; index++) {
          const result = await api.executionTrace(sessionId!, current.session, after, abort.signal)
          if (disposed) return
          if (!result.ok) { setError(result.copy ?? result.message); setLoading(false); return }
          rows = [...rows, ...result.data.entries]
          after = result.data.next_after ?? undefined
          more = result.data.has_more
          missing = result.data.context_missing
          if (!more) break
        }
        if (disposed) return
        setError('')
        setLevels(previous => previous.map((entry, index) => index === previous.length - 1 ? { ...entry, rows, after: after ?? null, more, missing } : entry))
        setLoading(false)
      } finally { if (!disposed) busy.current = false }
    }
    void load()
    const timer = window.setInterval(() => { if (!abort.signal.aborted) void load() }, 3000)
    return () => { disposed = true; abort.abort(); busy.current = false; window.clearInterval(timer) }
  }, [sessionId, current.session, active, visible, selected, refresh])
  useEffect(() => { const target = scroller(); if (target) target.scrollTop = current.scroll }, [current.session, selected])
  async function more() {
    if (!sessionId || busy.current || !current.after) return
    busy.current = true
    setLoading(true)
    const scope = current.session
    const abort = request.current
    try {
      const result = await api.executionTrace(sessionId, scope, current.after, abort?.signal)
      if (abort?.signal.aborted || levelsRef.current.at(-1)?.session !== scope) return
      if (!result.ok) { setError(result.copy ?? result.message); return }
      setLevels(previous => previous.map((entry, index) => index === previous.length - 1 ? { ...entry,
        rows: [...new Map([...entry.rows, ...result.data.entries].map(row => [row.id, row])).values()],
        after: result.data.next_after, more: result.data.has_more } : entry))
    } catch (failure) {
      if (!abort?.signal.aborted && request.current === abort) setError(failureText(failure))
    } finally {
      if (request.current === abort) { busy.current = false; setLoading(false) }
    }
  }
  const grouped = new Map<string, { label: string; rows: TraceEntry[] }>()
  for (const row of current.rows) {
    const start = new Date(row.time)
    start.setMinutes(0, 0, 0)
    const key = String(start.getTime())
    const end = new Date(start.getTime() + 3599999)
    const label = start.toLocaleDateString(getLocale()) + ' ' + start.toLocaleTimeString(getLocale(), { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
      + '–' + end.toLocaleTimeString(getLocale(), { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    if (!grouped.has(key)) grouped.set(key, { label, rows: [] })
    grouped.get(key)!.rows.push(row)
  }
  const groups = [...grouped.entries()]
  const requestNumbers = new Map<string, number>()
  for (const row of current.rows) if (row.kind === 'request') requestNumbers.set(row.id, requestNumbers.size + 1)
  function reveal(key: string, fetch: boolean) {
    setLevels(previous => previous.map((entry, index) => index === previous.length - 1
      ? { ...entry, shown: { ...entry.shown, [key]: (entry.shown[key] ?? 10) + 10 } } : entry))
    if (fetch) void more()
  }
  if (!sessionId) return <p className="text-xs text-muted">{t('ui.trace.selectSession')}</p>
  return <section ref={container} aria-label={t('ui.trace.title')} className="refresh-action-container space-y-3 min-w-0">
    <nav aria-label={t('ui.trace.path')} className="flex flex-wrap items-center gap-2 text-xs">
      {levels.map((entry, index) => <button key={entry.session ?? 'root'} type="button" className="text-brand break-all" onClick={() => { saveScroll(); setSelected(null); setLevels(previous => previous.slice(0, index + 1)) }}>{index ? '› ' : ''}{entry.label || (entry.session ?? t('ui.trace.main'))}</button>)}
      {!selected ? <RefreshButton size="xs" label={t('ui.trace.refresh')} className="ml-auto" disabled={loading} onClick={() => setRefresh(value => value + 1)} /> : null}
    </nav>
    {selected ? <TraceDetail key={selected} sessionId={sessionId} traceId={selected} requestNumber={requestNumbers.get(current.rows.find(row => row.request?.id === selected)?.id ?? '')} active={active && visible} onBack={() => setSelected(null)} onSelect={setSelected} /> : <>
      {error ? <p role="alert" className="text-xs text-danger">{noticeText(error)}</p> : null}
      {current.missing ? <p className="text-xs text-muted">{t('ui.trace.missing')}</p> : null}
      {!current.rows.length ? <p role="status" className="text-xs text-muted">{t(loading ? 'ui.trace.loading' : 'ui.trace.empty')}</p> : null}
      {groups.map(([key, group], index) => <details key={key} open={!current.collapsed[key]} onToggle={event => {
        const collapsed = !event.currentTarget.open
        setLevels(previous => previous.map((entry, position) => position === previous.length - 1 && entry.session === current.session && entry.collapsed[key] !== collapsed
          ? { ...entry, collapsed: { ...entry.collapsed, [key]: collapsed } } : entry))
      }} className="group space-y-2" data-trace-hour={key}>
        <summary className="activity-summary cursor-pointer list-none border-b border-line [&::-webkit-details-marker]:hidden">
          <ChevronRight aria-hidden className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-90" />
          <span>{group.label}</span>
        </summary>
        <ol className="ml-3 border-l border-line pl-4">
        {group.rows.slice(0, listVisibleCount(group.rows.length, current.shown[key] ?? 10)).map(row => <li key={row.id} data-trace-id={row.id} data-trace-kind={row.agent ? 'agent' : row.kind} className="relative border-b border-line py-3 space-y-2 text-xs min-w-0">
          {row.kind === 'request' ? <RequestNumberBadge number={requestNumbers.get(row.id)!} className="absolute -left-7 top-3" />
            : <span role="img" aria-label={t('ui.trace.' + (row.agent ? 'agent' : row.kind))}
              className={cn('absolute -left-7 top-3 flex h-6 w-6 items-center justify-center rounded-full border text-2xs font-semibold', row.agent ? 'border-brand/50 bg-raised text-brand' : 'border-line bg-raised text-muted')}>
              {row.agent ? <Bot aria-hidden className="h-3.5 w-3.5" /> : <Wrench aria-hidden className="h-3.5 w-3.5" />}
            </span>}
          <div className="flex flex-wrap items-center gap-2 text-2xs text-muted"><time title={new Date(row.time).toLocaleString(getLocale())}>{formatClock(row.time)}</time><span className={cn('rounded px-1.5 py-0.5 font-medium', row.kind === 'request' ? 'bg-brand/10 text-brand' : row.agent ? 'bg-brand/5 text-brand' : 'bg-raised text-muted')}>{row.request?.purpose === 'compaction' ? t('ui.components.inspector.RequestTracePanel.bddc29ad011a') : t('ui.trace.' + (row.agent ? 'agent' : row.kind))}</span><TraceStatusBadge status={row.status} />{(row.duration_ms ?? row.request?.observation?.duration_ms) != null ? <span>{formatDuration((row.duration_ms ?? row.request?.observation?.duration_ms)!)}</span> : null}</div>
          {row.kind === 'tool' ? <TraceToolName row={row} sessionId={sessionId} active={active && visible} /> : <div className="break-all font-medium">{row.name}</div>}
          {row.request ? <button type="button" className="text-brand" onClick={() => { saveScroll(); setSelected(row.request!.id) }}>{t('ui.trace.requestDetails')}</button> : null}
          {row.kind === 'tool' ? <>
            {row.trace_id ? <button type="button" className="text-brand break-all" onClick={() => { saveScroll(); setSelected(row.trace_id!) }}>{t('ui.trace.origin')}</button> : <p className="text-2xs text-muted">{t('ui.trace.noOrigin')}</p>}
            <details><summary className="cursor-pointer text-muted">{t('ui.trace.toolDetails')}</summary><ToolDetails sessionId={sessionId} agentSession={current.session} entry={row.id} revision={row.status} active={active && visible} /></details>
          </> : null}
          {row.agent ? row.agent.child_session_id ? <button type="button" className="text-brand break-all" onClick={() => { saveScroll(); setLevels(previous => [...previous, level(row.agent!.child_session_id!, row.agent!.description || row.agent!.agent_id)]) }}>{t('ui.trace.enterAgent')} · {row.agent.description || row.agent.agent_id} · {REQUEST_STATUS[row.agent.status] ?? t('ui.trace.status.' + row.agent.status)}</button> : <p className="text-muted">{t('ui.trace.missing')}</p> : null}
        </li>)}
        </ol>
        {group.rows.length > listVisibleCount(group.rows.length, current.shown[key] ?? 10) || index === groups.length - 1 && current.more
          ? <ShowMoreControl disabled={loading} onClick={() => reveal(key, group.rows.length <= listVisibleCount(group.rows.length, current.shown[key] ?? 10))} /> : null}
      </details>)}
    </>}
  </section>
}

function TraceToolName({ row, sessionId, active }: { row: TraceEntry; sessionId: string; active: boolean }) {
  const [loaded, setLoaded] = useState<{ identity: string; description: string } | null>(null)
  const attempted = useRef('')
  const controller = useRef<AbortController>()
  useEffect(() => () => controller.current?.abort(), [])
  useEffect(() => { if (!active) { controller.current?.abort(); attempted.current = '' } }, [active])
  useEffect(() => { controller.current?.abort(); attempted.current = ''; setLoaded(null) }, [sessionId, row.trace_id, row.name])
  const traceId = row.trace_id
  const identity = `${sessionId}:${traceId}:${row.name}`
  const description = row.description || (loaded?.identity === identity ? loaded.description : '')
  function loadDescription() {
    if (!active || description || !traceId) return
    if (attempted.current === identity) return
    attempted.current = identity
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    void api.requestTrace(sessionId, traceId, abort.signal, true).then(result => {
      if (abort.signal.aborted) return
      if (!result.ok) { attempted.current = ''; return }
      const match = result.data.tool_definitions?.find(tool => tool.name === row.name)
      if (match?.description) setLoaded({ identity, description: match.description })
    }).catch(() => { if (!abort.signal.aborted) attempted.current = '' })
  }
  return <ToolDescription description={description} focusable onTrigger={traceId && !row.description ? loadDescription : undefined}><span data-tool-name className="inline-block max-w-full break-all font-medium">{row.name}</span></ToolDescription>
}

function ToolDetails({ sessionId, agentSession, entry, revision, active }: { sessionId: string; agentSession?: string; entry: string; revision: string; active: boolean }) {
  useLocale()
  const [part, setPart] = useState('input')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState<RequestTracePart | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const host = useRef<HTMLDivElement>(null)
  const [opened, setOpened] = useState(false)
  useEffect(() => {
    const details = host.current?.closest('details')
    const update = () => setOpened(!!details?.open)
    details?.addEventListener('toggle', update)
    update()
    return () => details?.removeEventListener('toggle', update)
  }, [])
  useEffect(() => {
    if (!opened || !active) return
    const abort = new AbortController()
    setData(null); setError('')
    void api.executionTracePart(sessionId, entry, part, offset, agentSession, abort.signal).then(result => {
      if (abort.signal.aborted) return
      if (result.ok) setData(result.data)
      else setError(result.copy ?? result.message)
    })
    return () => abort.abort()
  }, [sessionId, agentSession, entry, part, offset, opened, revision, active])
  return <div ref={host} className="space-y-2 pt-2">
    <div className="flex gap-3">{['input', 'result'].map(value => <button key={value} type="button" aria-pressed={part === value} className="text-brand" onClick={() => { setPart(value); setOffset(0) }}>{t('ui.trace.' + value)}</button>)}</div>
    {error ? <p role="alert">{noticeText(error)}</p> : null}
    {data?.text ? isProbablyJson(data.text)
      ? <JsonSource text={data.text} active={active && opened} className="max-h-96" />
      : <pre tabIndex={0} className="max-h-96 overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere] rounded-lg border border-line bg-raised/35 p-3 font-mono text-xs leading-code text-muted">{data.text}</pre> : null}
    <div className="flex justify-between"><button type="button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 24000))}>{t('ui.trace.previous')}</button><button type="button" disabled={data?.next_offset == null} onClick={() => setOffset(data!.next_offset!)}>{t('ui.trace.next')}</button></div>
  </div>
}
