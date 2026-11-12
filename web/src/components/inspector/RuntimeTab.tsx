import {validateRuntimeHistory} from '@/lib/runtimeHistory'
import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { failureText } from '@/lib/resourceError'
import { t, useLocale } from '@/i18n'
/**
 * 运行时可观测面板。
 *
 * 模型在对话流之外还干了很多事：起常驻 PTY、派子代理、开后台作业。这些
 * 状态从对话流里看不见，用户就只能猜 —— 而"不敢给人看的后台任务"本身
 * 就是缺陷。这里把三类状态摆到明面上：
 *
 * * **终端** —— 展开后按行游标增量拉输出，跑到一半也能看，不阻塞轮询；
 * * **子代理** —— 谁被派出去、在跑还是已完成、用了多少次工具；
 * * **后台作业** —— 还在跑还是已经结束。
 *
 * 两个刻意的取舍：
 *
 * 1. **按需轮询 + 可见才轮询**。没有对话不读取；切走 tab 立即停，
 *    不留一个后台定时器空转。终端输出只在展开时按需拉，不预取。
 * 2. **终端输出使用有界尾窗**。按最新绝对行号替换窗口，兼顾缓冲淘汰、
 *    行数不变时的进度更新和未换行文本。读窗口不会消耗模型的输出。
 */

import { createContext, useContext, useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { Activity, CheckCircle2, CircleSlash, Clock, Loader2, Terminal, TriangleAlert } from 'lucide-react'
import type { JobOutputPayload, RuntimeJob, RuntimePayload, RuntimeSubagent, RuntimeTerminal, TerminalTailPayload } from '@/types'
import { ApiError } from '@/lib/http'
import { api, unwrap } from '@/lib/api'
import { cn, formatDuration } from '@/lib/utils'
import { useAppStore } from '@/store/app'
import { usePageVisible } from '@/hooks/usePageVisible'
import { Badge, Button, EmptyHint, RefreshButton, Section, ShowMoreControl } from '@/components/ui/primitives'
import { Markdown } from '@/components/markdown/Markdown'
import { agentErrorText, agentTree, mergeAgents, filterAgentTree, agentNeedsAttention, type AgentView } from '@/lib/subagentHistory'
import { mergeJobs, jobPresentation } from '@/lib/jobHistory'
import { terminalWaitNote } from '@/lib/commandResults'
import { DeferredPanel } from '@/components/ui/DeferredPanel'
import { loadRequests, loadContext } from './panelLoaders'

/** 快照轮询间隔。终端和作业都是秒级状态，2s 足够跟手又不至于刷屏。 */
const POLL_MS = 2_000
const RuntimeVisibility = createContext(true)

function useRuntimeVisible() {
  const active = useContext(RuntimeVisibility)
  return usePageVisible() && active
}

/** 运行时状态 + 一次拉取 + 一个错误位。 */
interface RuntimeState {
  sessionId: string | null
  data: RuntimePayload | null
  error: ResourceText | null
  /** 首次加载还没回来 —— 用来区分"还没拉到"和"确实没有"。 */
  loading: boolean
}

export function RuntimeTab({ active = true, showAgents = true }: { active?: boolean; showAgents?: boolean }) {
  useLocale()
  const pageVisible = usePageVisible() && active

  const sessionId = useAppStore((s) => s.activeSessionId)
  const jobTarget = useAppStore((s) => s.inspectorJobTarget)
  const [agentView, setAgentView] = useState<AgentView>('all')
  const [agentQuery, setAgentQuery] = useState('')
  const [reading, setReading] = useState(false)
  const refreshRuntime = useRef<(() => void) | null>(null)
  const actionRevision = useRef(0)
  const historyEpoch = useRef(0)
  const historyPaging = useRef({agents: false, jobs: false})
  const historyRead = useRef<AbortController>()
  const [branchChoices, setBranchChoices] = useState<Record<string, { active: boolean; collapsed: boolean }>>({})
  const [state, setState] = useState<RuntimeState>({ sessionId, data: null, error: null, loading: true })
  const [older, setOlder] = useState<{ owner: string | null; rows: RuntimeSubagent[]; cursor: string | null; hasMore: boolean | null; busy: boolean; error: ResourceText | null }>({ owner: sessionId, rows: [], cursor: null, hasMore: null, busy: false, error: null })
  const [olderJobs, setOlderJobs] = useState<{ owner: string | null; rows: RuntimeJob[]; cursor: string | null; hasMore: boolean | null; busy: boolean; error: ResourceText | null }>({ owner: sessionId, rows: [], cursor: null, hasMore: null, busy: false, error: null })

  useEffect(() => {
    historyEpoch.current += 1
    setState({ sessionId, data: null, error: null, loading: Boolean(sessionId) })
    setBranchChoices({})
    setAgentView('all')
    setAgentQuery('')
    setOlder({ owner: sessionId, rows: [], cursor: null, hasMore: null, busy: false, error: null })
    setOlderJobs({ owner: sessionId, rows: [], cursor: null, hasMore: null, busy: false, error: null })
  }, [sessionId])

  useEffect(() => {
    const controller = new AbortController()
    historyRead.current = controller
    historyPaging.current = {agents: false, jobs: false}
    if (!pageVisible) controller.abort()
    setOlder(previous => ({...previous, busy: false}))
    setOlderJobs(previous => ({...previous, busy: false}))
    return () => { historyEpoch.current++; controller.abort() }
  }, [sessionId, pageVisible])

  useEffect(() => {
    refreshRuntime.current = null
    if (!pageVisible || !sessionId) { setReading(false); return }
    let alive = true
    let inFlight = false
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      if (!alive || inFlight) return
      inFlight = true
      setReading(true)
      const revision = actionRevision.current
      try {
        const data = await unwrap(api.runtime(sessionId, controller.signal))
        if (alive && revision === actionRevision.current) {
          setState({ sessionId, data, error: null, loading: false })
          setOlder((prev) => prev.owner === sessionId && prev.hasMore !== null ? { ...prev, rows: mergeAgents(prev.rows, data.subagents) } : prev)
          setOlderJobs((prev) => prev.owner === sessionId && prev.hasMore !== null ? { ...prev, rows: mergeJobs(prev.rows, data.jobs) } : prev)
        }
      } catch (error) {
        if (alive && revision === actionRevision.current) setState((prev) => ({ ...prev, error: error instanceof ApiError && error.failure.copy ? error.failure.copy : error instanceof Error ? error.message : {key: 'ui.components.inspector.RuntimeTab.6aa27f14bff1'}, loading: false }))
      }
      inFlight = false
      if (alive) { setReading(false); timer = setTimeout(tick, POLL_MS) }
    }
    refreshRuntime.current = () => {
      if (!alive || inFlight) return
      clearTimeout(timer)
      void tick()
    }
    void tick()

    return () => {
      alive = false
      refreshRuntime.current = null
      controller.abort()
      if (timer) clearTimeout(timer)
    }
  }, [sessionId, pageVisible])
  const updateJob = useCallback((owner: string, job: RuntimeJob) => {
    if (useAppStore.getState().activeSessionId !== owner) return
    actionRevision.current += 1
    setOlderJobs((prev) => prev.owner === owner ? { ...prev, rows: mergeJobs(prev.rows, [job]) } : prev)
    setState((prev) => prev.sessionId === owner && prev.data ? { ...prev, data: { ...prev.data, jobs: prev.data.jobs.map((row) => row.id === job.id ? job : row) } } : prev)
  }, [])
  const updateTerminal = useCallback((owner: string, terminal: RuntimeTerminal) => {
    if (useAppStore.getState().activeSessionId !== owner) return
    actionRevision.current += 1
    setState((prev) => prev.sessionId === owner && prev.data ? {
      ...prev, data: { ...prev.data, terminals: prev.data.terminals.map((row) => row.sessionId === terminal.sessionId ? terminal : row) },
    } : prev)
  }, [])
  const updateSubagent = useCallback((owner: string, agent: RuntimeSubagent) => {
    if (useAppStore.getState().activeSessionId !== owner) return
    actionRevision.current += 1
    setOlder((prev) => prev.owner === owner && prev.hasMore !== null ? { ...prev, rows: mergeAgents(prev.rows, [agent]) } : prev)
    setState((prev) => prev.sessionId === owner && prev.data ? {
      ...prev, data: { ...prev.data, subagents: prev.data.subagents.map((row) => row.agent_id === agent.agent_id ? agent : row) },
    } : prev)
  }, [])
  const { data, error, loading } = state.sessionId === sessionId ? state : { data: null, error: null, loading: true }
  const loadOlder = async () => {
    const before = older.owner === sessionId && older.cursor ? older.cursor : data?.subagents_next_before
    const paging = historyPaging.current
    if (paging.agents || !sessionId || !before || older.busy || !pageVisible) return
    paging.agents = true
    const owner = sessionId
    const epoch = historyEpoch.current
    setOlder((prev) => ({ ...prev, busy: true, error: null }))
    try {
      const response = await api.subagentHistory(owner, before, historyRead.current?.signal)
      if (useAppStore.getState().activeSessionId !== owner || epoch !== historyEpoch.current) return
      if (response.ok) validateRuntimeHistory(response.data.agents, response.data.has_more, response.data.next_before, before, row => row.agent_id)
      setOlder((prev) => prev.owner !== owner ? prev : response.ok ? {
        owner, rows: mergeAgents(mergeAgents(prev.rows, data?.subagents ?? []), response.data.agents),
        cursor: response.data.next_before, hasMore: response.data.has_more, busy: false, error: null,
      } : { ...prev, busy: false, error: response.copy ?? response.message })
    } catch (failure) {
      if (useAppStore.getState().activeSessionId === owner && epoch === historyEpoch.current) setOlder(prev => prev.owner === owner ? {...prev, busy:false, error:failureText(failure)} : prev)
    } finally { paging.agents = false }
  }
  const loadOlderJobs = async () => {
    const before = olderJobs.owner === sessionId && olderJobs.cursor ? olderJobs.cursor : data?.jobs_next_before
    const paging = historyPaging.current
    if (paging.jobs || !sessionId || !before || olderJobs.busy || !pageVisible) return
    paging.jobs = true
    const owner = sessionId, epoch = historyEpoch.current
    setOlderJobs((prev) => ({ ...prev, busy: true, error: null }))
    try {
      const response = await api.jobHistory(owner, before, historyRead.current?.signal)
      if (useAppStore.getState().activeSessionId !== owner || epoch !== historyEpoch.current) return
      if (response.ok) validateRuntimeHistory(response.data.jobs, response.data.has_more, response.data.next_before, before, row => row.id)
      setOlderJobs((prev) => prev.owner !== owner ? prev : response.ok ? { owner,
        rows: mergeJobs(mergeJobs(prev.rows, data?.jobs ?? []), response.data.jobs), cursor: response.data.next_before,
        hasMore: response.data.has_more, busy: false, error: null } : { ...prev, busy: false, error: response.copy ?? response.message })
    } catch (failure) {
      if (useAppStore.getState().activeSessionId === owner && epoch === historyEpoch.current) setOlderJobs(prev => prev.owner === owner ? {...prev, busy:false, error:failureText(failure)} : prev)
    } finally { paging.jobs = false }
  }
  const focus = jobTarget?.sessionId === sessionId ? jobTarget : null

  const terminals = data?.terminals ?? []
  const subagents = useMemo(() => mergeAgents(older.owner === sessionId ? older.rows : [], data?.subagents ?? []), [older.owner, older.rows, sessionId, data?.subagents])
  const fullAgentTree = useMemo(() => agentTree(subagents), [subagents])
  const collapsedAgents = new Set(fullAgentTree.filter(row => {
    const choice = branchChoices[row.agent.agent_id]
    const active = row.activeDescendants > 0
    return row.descendants > 0 && (choice?.active === active ? choice.collapsed : !active)
  }).map(row => row.agent.agent_id))
  const visibleAgents = filterAgentTree(agentView === 'all' && !agentQuery.trim() ? agentTree(subagents, collapsedAgents) : fullAgentTree, agentView, agentQuery)
  // 聚焦详情已展示该作业，列表按身份排除，保留命令相同的其他作业。
  const jobs = mergeJobs(olderJobs.owner === sessionId ? olderJobs.rows : [], data?.jobs ?? [])
    .filter(job => job.id !== focus?.jobId)
  const hasAgentHistory = Boolean(older.hasMore ?? data?.subagents_has_more)
  const hasJobHistory = Boolean(olderJobs.hasMore ?? data?.jobs_has_more)

  const surface = (children: ReactNode) => <section aria-label={t('ui.components.inspector.Inspector.75b269496f69')} className="refresh-action-container min-w-0 space-y-4">
    <header className="flex items-center justify-between gap-2">
      <h3 className="ui-section-title">{t('ui.components.inspector.Inspector.75b269496f69')}</h3>
      {sessionId ? <RefreshButton size="xs" label={t('ui.runtime.refresh')} disabled={!pageVisible || reading} spinning={reading} onClick={() => refreshRuntime.current?.()} /> : null}
    </header>
    {children}
  </section>

  if (loading && !data && !focus) {
    return surface(
      <div role="status" className="flex items-center justify-center gap-2 py-10 text-xs text-faint">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />{t("ui.components.inspector.RuntimeTab.7210d33d82ad")}</div>
    )
  }

  if (error && !data && !focus) {
    return surface(
      <div role="alert"><EmptyHint
        icon={<TriangleAlert className="h-5 w-5" />}
        title={t("ui.components.inspector.RuntimeTab.a57238faf177")}
        description={noticeText(error)}
      /></div>
    )
  }

  if (!error && !terminals.length && (!showAgents || !subagents.length) && !jobs.length && !focus && !data?.jobs_history_error && !hasAgentHistory && !hasJobHistory) {
    return surface(
      <EmptyHint
        icon={<Activity className="h-5 w-5" />}
        title={t(!showAgents && subagents.length ? 'ui.member.openFromChat' : 'ui.components.inspector.RuntimeTab.dee44493c8d2')}
        description={
          !showAgents && subagents.length ? t('ui.member.openFromChatHint') : sessionId
            ? t("ui.components.inspector.RuntimeTab.96b9d75b744e")
            : t("ui.components.inspector.RuntimeTab.6b83f4fc0db1")
        }
      />
    )
  }

  return surface(
    <RuntimeVisibility.Provider value={active}><div className="space-y-5">
      {focus ? <Section title={t("ui.components.inspector.RuntimeTab.b5192dd1c8ef")} right={<Button size="xs" onClick={() => useAppStore.setState({ inspectorJobTarget: null })}>{t("ui.components.inspector.RuntimeTab.d83e8c497950")}</Button>}><div data-focused-job={focus.jobId}><FocusedJob key={`${focus.sessionId}:${focus.jobId}:${focus.seq}`} owner={focus.sessionId} id={focus.jobId} onUpdate={updateJob} /></div></Section> : null}
      {data?.jobs_history_error ? <p role="alert" className="text-xs text-danger">{data.jobs_history_error}</p> : null}
      {error ? (
        <p role="alert" className="flex items-start gap-1.5 rounded-lg border border-warn/25 bg-warn/8 px-3 py-2 text-xs leading-relaxed text-warn">
          <TriangleAlert aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span className="min-w-0 [overflow-wrap:anywhere]">{t("ui.components.inspector.RuntimeTab.922b53016ae2", {detail: noticeText(error)})}</span>
        </p>
      ) : null}

      {terminals.length ? (
        <Section
          title={t("ui.components.inspector.RuntimeTab.cec7d7de0e70")}
          right={
            <Badge tone={hasRunningTerminal(terminals) ? 'ok' : 'neutral'}>{terminals.length}</Badge>
          }
        >
          <div className="space-y-2">
            {terminals.map((terminal) => (
              <TerminalCard key={terminal.sessionId} terminal={terminal} ownerSessionId={sessionId} onUpdate={updateTerminal} />
            ))}
          </div>
        </Section>
      ) : null}

      {showAgents && (subagents.length || hasAgentHistory) ? (
        <Section
          title={t("ui.components.inspector.RuntimeTab.1b26650b7a56")}
            right={
              <Badge tone={hasActiveSubagents(subagents) ? 'brand' : 'neutral'}>{subagents.length}</Badge>
            }
        >
          <p className="mb-2 text-2xs leading-relaxed text-faint">{t("ui.components.inspector.RuntimeTab.12a4db4264c9")}</p>
          <div className="mb-3 space-y-2">
            <div role="group" aria-label={t('ui.runtime.agents.views')} className="flex flex-wrap gap-1">
              {(['all', 'active', 'attention'] as const).map(view => <button key={view} type="button" aria-pressed={agentView === view} onClick={() => setAgentView(view)} className={cn('rounded-md px-2.5 py-1.5 text-2xs transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand', agentView === view ? 'bg-brand/[0.12] text-brand' : 'text-muted hover:bg-raised hover:text-ink')}>
                {t('ui.runtime.agents.' + view)} <span className="ml-1 tabular-nums">{view === 'all' ? subagents.length : subagents.filter(agent => view === 'active' ? ['pending', 'running'].includes(agent.status) : agentNeedsAttention(agent)).length}</span>
              </button>)}
            </div>
            <input type="search" value={agentQuery} onChange={event => setAgentQuery(event.target.value)} aria-label={t('ui.runtime.agents.search')} placeholder={t('ui.runtime.agents.search')} className="ui-field placeholder:text-faint focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand" />
            <p className="text-2xs text-faint">{t('ui.runtime.agents.scope')}</p>
          </div>
          {!visibleAgents.length && <p role="status" className="py-4 text-center text-xs text-muted">{t('ui.runtime.agents.empty')}</p>}
          <div className="space-y-2">
            {visibleAgents.map(row => <div key={`${sessionId}:${row.agent.agent_id}`} data-agent-depth={row.depth} style={{ marginLeft: Math.min(row.depth, 3) * 8 }} className={cn('min-w-0', row.depth > 0 && 'border-l border-line pl-2')}>
              {row.missingParent ? <p className="mb-1 break-all text-2xs text-faint">{t("ui.components.inspector.RuntimeTab.3c6506b5f60f")}{row.agent.parent_agent_id}</p> : row.brokenRelation ? <p className="mb-1 text-2xs text-warn">{t("ui.components.inspector.RuntimeTab.cbfd4d6a88b1")}</p> : row.parentDescription ? <p className="mb-1 truncate text-2xs text-faint" title={row.parentDescription}>{t('ui.runtime.agents.delegatedBy', {parent: row.parentDescription, depth: row.depth + 1})}</p> : null}
              {row.descendants && agentView === 'all' && !agentQuery.trim() ? <button type="button" aria-expanded={!collapsedAgents.has(row.agent.agent_id)} onClick={() => setBranchChoices(previous => ({ ...previous, [row.agent.agent_id]: { active: row.activeDescendants > 0, collapsed: !collapsedAgents.has(row.agent.agent_id) } }))} className="mb-1 text-2xs text-brand">{t(collapsedAgents.has(row.agent.agent_id) ? (row.descendants === 1 ? "ui.runtime.expandOneDescendant" : "ui.runtime.expandDescendants") : (row.descendants === 1 ? "ui.runtime.collapseOneDescendant" : "ui.runtime.collapseDescendants"), {count: row.descendants})}{row.activeDescendants ? t("ui.components.inspector.RuntimeTab.847ed45ef95b", {p0: (row.activeDescendants)}) : ''}</button> : null}
              <SubagentCard agent={row.agent} ownerSessionId={sessionId} onUpdate={updateSubagent} activeDescendants={row.activeDescendants} />
            </div>)}
          </div>
          {sessionId && (older.hasMore ?? data?.subagents_has_more) ? <ShowMoreControl disabled={older.busy} onClick={() => void loadOlder()}>{older.busy ? t("ui.components.inspector.RuntimeTab.79cfd79d781e") : t("ui.showMore")}</ShowMoreControl> : null}
          {older.owner === sessionId && older.error ? <p role="alert" className="mt-2 text-2xs text-danger">{noticeText(older.error)}</p> : null}
        </Section>
      ) : null}

      {jobs.length || hasJobHistory ? (
        <Section title={t("ui.components.inspector.RuntimeTab.47fa6f922d42")} right={<Badge tone="neutral">{jobs.length}</Badge>}>
          <div className="space-y-2">
            {jobs.map((job) => (
              <JobCard key={`${sessionId}:${job.id}`} job={job} ownerSessionId={sessionId} onUpdate={updateJob} />
            ))}
          </div>
          {sessionId && (olderJobs.hasMore ?? data?.jobs_has_more) ? <ShowMoreControl disabled={olderJobs.busy} onClick={() => void loadOlderJobs()}>{olderJobs.busy ? t("ui.components.inspector.RuntimeTab.79cfd79d781e") : t("ui.showMore")}</ShowMoreControl> : null}
          {olderJobs.owner === sessionId && olderJobs.error ? <p role="alert" className="mt-2 text-2xs text-danger">{noticeText(olderJobs.error)}</p> : null}
        </Section>
      ) : null}
    </div></RuntimeVisibility.Provider>
  )
}

/* ============================ 终端 ============================ */

function hasRunningTerminal(terminals: RuntimeTerminal[]): boolean {
  return terminals.some((t) => t.status.kind === 'running')
}

function hasActiveSubagents(subagents: RuntimeSubagent[]): boolean {
  return subagents.some((a) => a.status === 'running' || a.status === 'pending')
}

/**
 * 一个常驻终端。展开时串行读取有界尾窗，完整行与未换行进度一起展示。
 */
function TerminalCard({
  terminal,
  ownerSessionId,
  onUpdate,
}: {
  terminal: RuntimeTerminal
  ownerSessionId: string | null
  onUpdate: (owner: string, terminal: RuntimeTerminal) => void
}) {
  useLocale()
  const pageVisible = useRuntimeVisible()

  const [open, setOpen] = useState(false)
  const [output, setOutput] = useState<TerminalTailPayload | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [busy, setBusy] = useState(false)
  const [stopping, setStopping] = useState(false)
  const stopLock = useRef(false)
  const [error, setError] = useState<ResourceText | null>(null)
  const [actionError, setActionError] = useState<ResourceText | null>(null)
  const scroller = useRef<HTMLDivElement | null>(null)
  const pinned = useRef(true)

  const exited = terminal.status.kind === 'exited'
  const exitCode = terminal.status.exit_code ?? null
  const cleanupFailed = terminal.control?.state === 'cleanup-failed'
  const isStopping = stopping || terminal.control?.state === 'stopping'
  const label = isStopping ? t("ui.components.inspector.RuntimeTab.af48159f380f") : cleanupFailed ? t("ui.components.inspector.RuntimeTab.601747ffd99d") : exited ? t("ui.components.inspector.RuntimeTab.be3a647485ac", {p0: (exitCode == null ? '' : ` ${exitCode}`)}) : t("ui.components.inspector.RuntimeTab.1f0eb99b7ed0")

  // 展开期间持续跟读；收起立刻停，避免每个折叠的终端都在轮询。
  useEffect(() => {
    if (!open || !pageVisible) return
    let alive = true
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const pull = async () => {
      if (!alive) return
      setBusy(true)
      const response = await api.terminalTail(ownerSessionId, terminal.sessionId, controller.signal)
      if (!alive) return
      if (response.ok) { setOutput(response.data); setError(null) }
      else setError(response.copy ?? response.message)
      setBusy(false)
      if (!exited || cleanupFailed) timer = setTimeout(pull, POLL_MS)
    }
    void pull()
    return () => {
      alive = false
      controller.abort()
      if (timer) clearTimeout(timer)
    }
  }, [open, ownerSessionId, terminal.sessionId, exited, cleanupFailed, refresh, pageVisible])
  const stopTerminal = async () => {
    if (!ownerSessionId || stopping || stopLock.current) return
    stopLock.current = true
    setStopping(true)
    try {
      const response = await api.stopTerminal(ownerSessionId, terminal.sessionId)
      if (response.ok) {
        onUpdate(ownerSessionId, response.data.terminal)
        setActionError(response.data.audit_error != null ? {key: 'ui.audit.terminalStopIncomplete'} : null)
      } else setActionError(response.copy ?? response.message)
    } catch (failure) {
      setActionError(failureText(failure))
    } finally {
      stopLock.current = false
      setStopping(false)
    }
  }

  // 跟到底部时自动滚过去；用户往回翻就不打扰。
  useEffect(() => {
    const node = scroller.current
    if (node && pinned.current) node.scrollTop = node.scrollHeight
  }, [output])
  useEffect(() => {
    const node = scroller.current
    if (!open || !node) return
    const observer = new ResizeObserver(() => {
      if (pinned.current) node.scrollTop = node.scrollHeight
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [open])
  const text = output ? output.text + (output.pendingText ? (output.text ? '\n' : '') + output.pendingText : '') : ''

  return (
    <div
      data-terminal-id={terminal.sessionId}
      className={cn(
        'overflow-hidden rounded-xl border bg-raised/35 transition-colors',
        open ? 'border-line' : 'border-line/70',
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-raised/60"
      >
        <span
          className={cn(
            'flex h-6 w-6 shrink-0 items-center justify-center rounded-lg',
            exited ? 'bg-muted/10 text-faint' : 'bg-ok/[0.12] text-ok',
          )}
        >
          {exited ? <CircleSlash className="h-3.5 w-3.5" /> : <Terminal className="h-3.5 w-3.5" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-xs font-semibold text-ink">
              {terminal.name ?? terminal.sessionId}
            </span>
            <Badge tone={cleanupFailed ? 'danger' : exited ? 'neutral' : 'ok'}>
              {label}
            </Badge>
          </span>
          <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-faint">
            <span className="min-w-0 truncate font-mono" title={terminal.cwd}>{terminal.cwd}</span>
            <span className="shrink-0 tabular-nums">PID {terminal.pid}</span>
          </span>
        </span>
        <span className="shrink-0 text-faint">
          <Chevron open={open} />
        </span>
      </button>

      {ownerSessionId ? <button className="mx-3 mb-2 text-2xs text-brand hover:underline" type="button" onClick={() => useAppStore.getState().revealTerminal(ownerSessionId, terminal.sessionId)}>{t("ui.components.inspector.RuntimeTab.58357f4a77eb")}</button> : null}
      {!exited || cleanupFailed ? <div className="flex gap-1 px-2 pb-2">
        <Button size="xs" disabled={!ownerSessionId || isStopping} onClick={() => void stopTerminal()}>
          {cleanupFailed ? t("ui.components.inspector.RuntimeTab.78a3a1698396") : t("ui.components.inspector.RuntimeTab.2993cb23dc3e")}
        </Button>
      </div> : null}
      {terminal.control?.error ? <p role="alert" className="break-words px-3 pb-2 text-2xs text-danger">{t("ui.components.inspector.RuntimeTab.96653e9300bf")}{terminal.control.error}</p> : null}
      {actionError ? <p role="alert" className="break-words px-3 pb-2 text-2xs text-danger">{noticeText(actionError)}</p> : null}

      {open ? (
        <div className="border-t border-line/70">
          <div
            ref={scroller}
            tabIndex={0}
            aria-label={t("ui.components.inspector.RuntimeTab.5aaeac6ca717", {p0: (terminal.name ?? terminal.sessionId)})}
            onScroll={(event) => {
              const node = event.currentTarget
              pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 24
            }}
            className="no-scrollbar max-h-panel overflow-auto bg-canvas/45 px-3 py-2.5"
          >
            {error ? (
              <p className="py-2 text-2xs text-danger">{noticeText(error)}</p>
            ) : text ? (
              <pre aria-label={t("ui.components.inspector.RuntimeTab.9813621ec99f", {p0: (terminal.sessionId)})} className="whitespace-pre-wrap break-all font-mono text-xs leading-relaxed text-muted">
                {text}
              </pre>
            ) : (
              <p className="py-2 text-2xs text-faint">
                {busy ? t("ui.components.inspector.RuntimeTab.86b6d0d63062") : t("ui.components.inspector.RuntimeTab.ac41b6f2cbe2")}
              </p>
            )}
          </div>
          <div className="refresh-action-container flex items-center justify-between gap-2 border-t border-line/60 px-3 py-1.5 text-2xs text-faint">
            <span className="tabular-nums">{t(output?.totalLines === 1 ? "ui.runtime.outputOneLine" : "ui.runtime.outputLines", {count: output?.totalLines ?? 0})}{output?.pendingText ? t("ui.components.inspector.RuntimeTab.75940529e83b") : ''}
              {output?.truncated ? t("ui.components.inspector.RuntimeTab.30e7245d311b") : ''}
            </span>
            <div className="flex shrink-0 items-center gap-1">
            <button type="button" onClick={() => {
              pinned.current = true
              if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight
            }} className="rounded px-1.5 py-0.5 transition-colors hover:bg-raised hover:text-muted">{t("ui.components.inspector.RuntimeTab.9d562f04bdfd")}</button>
            <RefreshButton size="xs" variant="ghost" label={t("ui.components.inspector.RuntimeTab.aee887434131")} onClick={() => setRefresh((value) => value + 1)} disabled={busy} spinning={busy} />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function Chevron({ open }: { open: boolean }) {
  useLocale()

  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 200ms ease' }}
    >
      <path
        d="M4.5 2.5 8 6l-3.5 3.5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/* ============================ 子代理 ============================ */

const SUBAGENT_TONE: Record<RuntimeSubagent['status'], 'neutral' | 'ok' | 'warn' | 'danger' | 'brand'> = {
  pending: 'neutral',
  running: 'brand',
  completed: 'ok',
  failed: 'danger',
  interrupted: 'warn',
}

const SUBAGENT_LABEL_KEYS: Record<RuntimeSubagent['status'], string> = {
  pending: 'ui.member.status.pending',
  running: 'ui.member.status.running',
  completed: 'ui.member.status.done',
  failed: 'ui.member.status.failed',
  interrupted: 'ui.member.status.interrupted',
}

function SubagentCard({ agent, ownerSessionId, onUpdate, activeDescendants = 0 }: { agent: RuntimeSubagent; ownerSessionId: string | null; onUpdate: (owner: string, agent: RuntimeSubagent) => void; activeDescendants?: number }) {
  useLocale()
  const pageVisible = useRuntimeVisible()

  const [requestsOpen, setRequestsOpen] = useState(false)
  const [contextOpen, setContextOpen] = useState(false)
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<(RuntimeSubagent & { prompt: string }) | null>(null)
  const [error, setError] = useState<ResourceText | null>(null)
  const [actionError, setActionError] = useState<ResourceText | null>(null)
  const [busy, setBusy] = useState(false)
  const stopLock = useRef(false)
  const actionRevision = useRef(0)
  const done = agent.status === 'completed' || agent.status === 'failed' || agent.status === 'interrupted'
  useEffect(() => {
    if (!open || !ownerSessionId || !pageVisible) return
    let alive = true
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const pull = async () => {
      const revision = actionRevision.current
      const response = await api.subagentDetail(ownerSessionId, agent.agent_id, controller.signal)
      if (!alive) return
      if (revision === actionRevision.current) {
        if (response.ok) { setDetail(response.data.agent); setError(null) }
        else setError(response.copy ?? response.message)
      }
      if (!done) timer = setTimeout(pull, POLL_MS)
    }
    void pull()
    return () => { alive = false; controller.abort(); if (timer) clearTimeout(timer) }
  }, [open, ownerSessionId, agent.agent_id, done, agent.status, pageVisible])
  const stopAgent = async () => {
    if (!ownerSessionId || busy || stopLock.current) return
    stopLock.current = true
    setBusy(true)
    try {
      const response = await api.stopSubagent(ownerSessionId, agent.agent_id)
      if (response.ok) {
        actionRevision.current += 1
        setDetail(response.data.agent)
        onUpdate(ownerSessionId, response.data.agent)
        setActionError(response.data.audit_error != null ? {key: 'ui.audit.agentStopIncomplete'} : null)
      } else setActionError(response.copy ?? response.message)
    } catch (failure) {
      setActionError(failureText(failure))
    } finally {
      stopLock.current = false
      setBusy(false)
    }
  }
  return (
    <div data-agent-id={agent.agent_id} className="min-w-0 rounded-xl border border-line/70 bg-raised/35 px-3 py-2.5">
      <div className="flex items-start gap-2">
        <span
          className={cn(
            'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg',
            done ? 'bg-muted/10 text-faint' : 'bg-brand/[0.12] text-brand',
          )}
        >
          {agent.status === 'failed' ? <TriangleAlert className="h-3.5 w-3.5 text-danger" /> : agent.status === 'interrupted' ? <CircleSlash className="h-3.5 w-3.5" /> : done ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">
              {agent.description || agent.agent_id}
            </p>
            <Badge tone={SUBAGENT_TONE[agent.status]}>{agent.recovered ? t("ui.components.inspector.RuntimeTab.f4237d23edbb") : t(SUBAGENT_LABEL_KEYS[agent.status])}</Badge>
          </div>
          {agent.archived ? <p className="mt-1 text-2xs text-faint">{t("ui.components.inspector.RuntimeTab.6c845e82a658")}</p> : null}
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-2xs text-faint">
            <span className="tabular-nums">
              <Clock className="mr-1 inline h-2.5 w-2.5" />
              {agent.duration_ms == null ? t("ui.components.inspector.RuntimeTab.541e6553303b") : formatDuration(agent.duration_ms)}
            </span>
            <span className="tabular-nums">{t(agent.tool_calls === 1 ? "ui.runtime.oneToolCall" : "ui.runtime.toolCalls", {count: agent.tool_calls})}</span>
            {agent.pending_messages ? (
              <span className="tabular-nums">{t(agent.pending_messages === 1 ? "ui.runtime.onePendingMessage" : "ui.runtime.pendingMessages", {count: agent.pending_messages})}</span>
            ) : null}
          </div>
          {agent.error ? (
            <p className="mt-1.5 break-words rounded-lg bg-danger/8 px-2 py-1 text-2xs leading-relaxed text-danger">
              {agentErrorText(agent)}
            </p>
          ) : null}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        <Button size="xs" disabled={!ownerSessionId} aria-expanded={open} aria-controls={`agent-detail-${agent.agent_id}`} onClick={() => setOpen((value) => !value)}>{open ? t("ui.components.inspector.RuntimeTab.0ae7312510f4") : t("ui.components.inspector.RuntimeTab.a748cc074f78")}</Button>
        {agent.child_session_id ? <Button size="xs" disabled={!ownerSessionId} aria-expanded={requestsOpen} aria-controls={`agent-requests-${agent.agent_id}`} onClick={() => { setRequestsOpen(value => !value); setContextOpen(false) }}>{requestsOpen ? t("ui.components.inspector.RuntimeTab.17ff19c40aaf") : t("ui.components.inspector.RuntimeTab.3186fef1a2be")}</Button> : null}
        {agent.context_persisted ? <Button size="xs" disabled={!ownerSessionId} aria-expanded={contextOpen} aria-controls={`agent-context-${agent.agent_id}`} onClick={() => { setContextOpen(value => !value); setRequestsOpen(false) }}>{contextOpen ? t("ui.components.inspector.RuntimeTab.b0d2627e2d69") : t("ui.components.inspector.RuntimeTab.d33fc4a0b315")}</Button> : null}
        {!done || activeDescendants > 0 ? <Button size="xs" disabled={!ownerSessionId || busy} onClick={() => void stopAgent()}>{busy ? t("ui.components.inspector.RuntimeTab.8a99be840844") : activeDescendants > 0 ? t("ui.components.inspector.RuntimeTab.0c91067e40c6") : t("ui.components.inspector.RuntimeTab.11820a8941c3")}</Button> : null}
      </div>
      {actionError ? <p role="alert" className="mt-2 break-words text-2xs text-danger">{noticeText(actionError)}</p> : null}
      {agent.archive_error ? <p role="alert" className="mt-2 break-words text-2xs text-warn">{noticeText(agent.archive_error_copy ?? agent.archive_error)}</p> : null}
      {requestsOpen && agent.child_session_id ? <div id={`agent-requests-${agent.agent_id}`} className="mt-3 border-t border-line pt-3"><DeferredPanel load={loadRequests} panelProps={{ sessionId: ownerSessionId, agentSessionId: agent.child_session_id, active: pageVisible }} label={t('ui.trace.title')} /></div> : null}
      {contextOpen && ownerSessionId ? <div id={`agent-context-${agent.agent_id}`} className="mt-3 border-t border-line pt-3"><DeferredPanel load={loadContext} panelProps={{ sessionId: ownerSessionId, agentId: agent.agent_id, active: pageVisible }} label={t('ui.components.inspector.Inspector.6fc38ef3602f')} /></div> : null}
      {open ? <div id={`agent-detail-${agent.agent_id}`} className="mt-2 min-w-0 space-y-2 border-t border-line pt-2">
        {error ? <p role="alert" className="break-words text-2xs text-danger">{noticeText(error)}</p> : null}
        {!detail ? <p className="text-2xs text-faint">{t("ui.components.inspector.RuntimeTab.6f160ba76bd9")}</p> : <>
          <p className="text-2xs font-semibold text-faint">{t("ui.components.inspector.RuntimeTab.5161967c2cdf")}</p>
          {!agent.child_session_id ? <p className="text-2xs text-faint">{t("ui.components.inspector.RuntimeTab.a45c97497a84")}</p> : null}
          <div tabIndex={0} aria-label={t("ui.components.inspector.RuntimeTab.2f024abda44a")} className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-muted">{detail.prompt}</div>
          <p className="text-2xs font-semibold text-faint">{done ? t("ui.components.inspector.RuntimeTab.50d50f8fcf0d") : t("ui.components.inspector.RuntimeTab.fd61194e349f")}</p>
          <div tabIndex={0} aria-label={t("ui.components.inspector.RuntimeTab.5f97cfec6468")} className="max-h-runtime-panel min-w-0 overflow-auto rounded-lg bg-canvas/45 p-2 [overflow-wrap:anywhere]">
            {detail.result ? <Markdown dense>{detail.result}</Markdown> : <p className="text-xs text-faint">{done ? t("ui.components.inspector.RuntimeTab.aa53aa051a63") : t("ui.components.inspector.RuntimeTab.848ec6a854bf")}</p>}
          </div>
          {detail.error ? <p className="break-words text-2xs text-danger">{detail.error}</p> : null}
        </>}
        <p className="text-2xs leading-relaxed text-faint">{t("ui.components.inspector.RuntimeTab.6cfce5050c4b")}</p>
      </div> : null}
    </div>
  )
}

/* ============================ 后台作业 ============================ */

function FocusedJob({ owner, id, onUpdate }: { owner: string; id: string; onUpdate: (owner: string, job: RuntimeJob) => void }) {
  useLocale()
  const pageVisible = useRuntimeVisible()

  const [state, setState] = useState<{ job?: RuntimeJob; error?: ResourceText }>({})
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!pageVisible) return
    let alive = true
    const controller = new AbortController()
    setState({})
    void api.jobOutput(owner, id, controller.signal).then(response => {
      if (!alive) return
      setState(response.ok && response.data.job.id === id ? { job: response.data.job } : { error: response.ok ? {key: 'ui.components.inspector.RuntimeTab.a06f2c51e9b6'} : response.copy ?? response.message })
    })
    return () => { alive = false; controller.abort() }
  }, [owner, id, retry, pageVisible])
  return state.job ? <JobCard job={state.job} ownerSessionId={owner} defaultOpen onUpdate={(session, job) => { setState({ job }); onUpdate(session, job) }} />
    : state.error ? <div role="alert" className="space-y-2 text-xs text-danger"><p>{noticeText(state.error)}</p><Button size="xs" onClick={() => setRetry(value => value + 1)}>{t("ui.components.inspector.RuntimeTab.e71cdb3d5e6a")}</Button></div>
    : <p role="status" className="text-xs text-faint">{t("ui.components.inspector.RuntimeTab.163709e6cd9f")}</p>
}

function JobCard({ job, ownerSessionId, onUpdate, defaultOpen = false }: { job: RuntimeJob; ownerSessionId: string | null; onUpdate: (owner: string, job: RuntimeJob) => void; defaultOpen?: boolean }) {
  useLocale()
  const pageVisible = useRuntimeVisible()

  const [open, setOpen] = useState(defaultOpen)
  const [output, setOutput] = useState<JobOutputPayload | null>(null)
  const [error, setError] = useState<ResourceText | null>(null)
  const [actionError, setActionError] = useState<ResourceText | null>(null)
  const [busy, setBusy] = useState(false)
  const stopLock = useRef(false)
  const [reload, setReload] = useState(0)
  const scroller = useRef<HTMLPreElement | null>(null)
  const pinned = useRef(true)
  const revision = useRef(0)
  const outputId = useId()
  const current = jobPresentation(job).done ? job : output?.job ?? job
  const { done, failed, unavailable, terminalWait, label } = jobPresentation(current)
  const cleanupFailed = current.status === 'cleanup-failed'
  useEffect(() => {
    if (!open || !ownerSessionId || !pageVisible) return
    let alive = true
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const pull = async () => {
      const version = revision.current
      const response = await api.jobOutput(ownerSessionId, job.id, controller.signal)
      if (!alive || version !== revision.current) return
      if (response.ok && response.data.job.id === job.id) { setOutput(response.data); setError(null) }
      else if (response.ok) setError({key: 'ui.components.inspector.RuntimeTab.c074f5d1f54d'})
      else setError(response.copy ?? response.message)
      if (!done && (!response.ok || !jobPresentation(response.data.job).done)) timer = setTimeout(pull, POLL_MS)
    }
    void pull()
    return () => { alive = false; controller.abort(); if (timer) clearTimeout(timer) }
  }, [open, ownerSessionId, job.id, done, job.status, reload, pageVisible])
  useEffect(() => {
    if (scroller.current && pinned.current) scroller.current.scrollTop = scroller.current.scrollHeight
  }, [output])
  const stopJob = async () => {
    if (!ownerSessionId || busy || stopLock.current) return
    stopLock.current = true
    setBusy(true)
    try {
      const response = await api.stopJob(ownerSessionId, job.id)
      if (response.ok) {
        revision.current += 1
        setOutput(prev => prev ? { ...prev, job: response.data.job } : prev)
        setReload(value => value + 1)
        onUpdate(ownerSessionId, response.data.job)
        setActionError(response.data.audit_error != null ? {key: 'ui.audit.jobStopIncomplete'} : response.data.outcome === 'cleanup-failed' ? {key: 'ui.components.inspector.RuntimeTab.2a2ae22da520'} : null)
      } else setActionError(response.copy ?? response.message)
    } catch (failure) {
      setActionError(failureText(failure))
    } finally {
      stopLock.current = false
      setBusy(false)
    }
  }
  return (
    <div data-job-id={job.id} className="overflow-hidden rounded-xl border border-line/70 bg-raised/35">
      <div className="flex items-center gap-2 px-3 py-2">
      <span
        className={cn(
          'flex h-6 w-6 shrink-0 items-center justify-center rounded-lg',
          done ? 'bg-muted/10 text-faint' : 'bg-warn/[0.12] text-warn',
        )}
      >
        {failed || unavailable ? <TriangleAlert className="h-3.5 w-3.5 text-warn" /> : done ? terminalWait ? <Clock className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" /> : <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-ink">{job.label || job.id}</span>
        {current.detail ? (
          <span className="mt-0.5 block truncate font-mono text-2xs text-faint" title={current.detail}>{current.detail}</span>
        ) : null}
      </span>
      <Badge tone={failed ? 'danger' : unavailable ? 'warn' : done ? 'neutral' : 'warn'} className="shrink-0">
        {busy ? t("ui.components.inspector.RuntimeTab.af48159f380f") : label}
      </Badge>
      </div>
      {current.archived ? <p className="px-3 pb-2 text-2xs text-faint">{t("ui.components.inspector.RuntimeTab.c482b6def1ce")}{unavailable ? t("ui.components.inspector.RuntimeTab.5e39e10e683e") : ''}</p> : null}
      {terminalWait && !unavailable ? <p className="px-3 pb-2 text-2xs leading-5 text-warn">{terminalWaitNote(current.wait?.reason)}</p> : null}
      {current.encoding_errors?.map((issue, index) => <p key={index} role="alert" className="px-3 pb-2 text-2xs text-danger">{t('ui.encoding.failure', {...issue})}</p>)}
      {current.archiveError ? <p role="alert" className="px-3 pb-2 text-2xs text-danger">{current.archiveError}</p> : null}
      {job.scope === 'foreground-cleanup' ? (
        <p className={cn('mx-3 mb-2 rounded-lg px-2.5 py-2 text-xs leading-relaxed', done && !unavailable ? 'bg-brand/8 text-muted' : 'bg-warn/8 text-warn')}>
          {unavailable ? t("ui.components.inspector.RuntimeTab.4844d8c8edee") : done ? t("ui.components.inspector.RuntimeTab.6782fb93c375") : t("ui.components.inspector.RuntimeTab.48f0cae9d4e3")}
        </p>
      ) : null}
      <div className="flex gap-1 px-2 pb-2">
        <Button size="xs" disabled={!ownerSessionId} aria-expanded={open} aria-controls={outputId} onClick={() => setOpen((value) => !value)}>{open ? t("ui.components.inspector.RuntimeTab.0c9cefe2722d") : t("ui.components.inspector.RuntimeTab.d70cf6ebc7af")}</Button>
        {current.terminalId && !current.archived ? <Button size="xs" disabled={!ownerSessionId} onClick={() => ownerSessionId && useAppStore.getState().revealTerminal(ownerSessionId, current.terminalId!)}>{t("ui.components.inspector.RuntimeTab.58357f4a77eb")}</Button> : null}
        {!done ? <Button size="xs" disabled={!ownerSessionId || busy || current.status === 'stopping'} onClick={() => void stopJob()}>{cleanupFailed ? t("ui.components.inspector.RuntimeTab.7cf6edbc2a00") : t("ui.components.inspector.RuntimeTab.a33b3101a358")}</Button> : null}
      </div>
      {current.cleanupError ? <p className="break-words px-3 pb-2 text-2xs text-danger">{current.detail} {current.cleanupError}</p> : null}
      {error ? <p role="alert" className="px-3 pb-2 text-2xs text-danger">{noticeText(error)}</p> : null}
      {error && open ? <Button size="xs" onClick={() => setReload(value => value + 1)}>{t("ui.components.inspector.RuntimeTab.ef425a92f5ab")}</Button> : null}
      {actionError ? <p role="alert" className="px-3 pb-2 text-2xs text-danger">{noticeText(actionError)}</p> : null}
      {open ? <div id={outputId} className="border-t border-line px-3 py-2">
        {output?.truncated ? <p className="mb-1 text-2xs text-faint">{t("ui.components.inspector.RuntimeTab.7cc5a37d09cb")}</p> : null}
        <pre ref={scroller} onScroll={() => { const node = scroller.current; if (node) pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 16 }} aria-label={t("ui.components.inspector.RuntimeTab.919c3e5de8f0", {p0: (job.id)})} className="max-h-40 overflow-auto whitespace-pre-wrap break-all font-mono text-2xs text-muted">{output ? output.stdout + (output.stderr ? '\n[stderr]\n' + output.stderr : '') || t("ui.components.inspector.RuntimeTab.03dac0f7765a") : t("ui.components.inspector.RuntimeTab.41a6ca4272a9")}</pre>
      </div> : null}
    </div>
  )
}
