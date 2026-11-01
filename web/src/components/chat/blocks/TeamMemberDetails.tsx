import {agentErrorText} from '@/lib/subagentHistory'
import type {ResourceText} from '@/types'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { usePageVisible } from '@/hooks/usePageVisible'
import { t, useLocale } from '@/i18n'
import { formatDuration } from '@/lib/utils'
import { Badge, Button, RefreshButton } from '@/components/ui/primitives'
import { Markdown } from '@/components/markdown/Markdown'
import { DeferredPanel } from '@/components/ui/DeferredPanel'
import { loadRequests } from '@/components/inspector/panelLoaders'
import { blocksFromDetail } from '@/store/chat'
import { MemberConversation } from './MemberConversation'
import type { Block, RuntimeSubagent } from '@/types'

/** 最近一次流式文本，已完成成员的结尾快照也会保留到运行结束。 */
export type TeamMemberPreview = { text: string; thinking?: string; truncated: boolean; live?: boolean }

type TeamMemberDetailsProps = {
  owner: string; id: string; name: string; agent?: string; role?: string; leadName?: string; leadAgent?: string; contextSessionId?: string; preview?: TeamMemberPreview; embedded?: boolean; active?: boolean
}

export const TeamMemberDetails = memo(function TeamMemberDetails(props: TeamMemberDetailsProps) {
  return <MemberDetails key={JSON.stringify([props.owner, props.id])} {...props} />
})

function MemberDetails({ owner, id, name, agent, role, leadName, leadAgent, contextSessionId, preview, embedded = false, active = true }: TeamMemberDetailsProps) {
  useLocale()
  const pageVisible = usePageVisible()
  const [open, setOpen] = useState(embedded)
  const [data, setData] = useState<(RuntimeSubagent & {prompt: string}) | null>(null)
  const [conversation, setConversation] = useState<Block[] | null>(null)
  const [conversationError, setConversationError] = useState(false)
  const [recent, setRecent] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [loading, setLoading] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [controlError, setControlError] = useState<ResourceText>('')
  const stopLock = useRef(false)
  const mounted = useRef(true)
  const observing = useRef(false)
  observing.current = embedded && active && pageVisible
  const [tracesOpen, setTracesOpen] = useState(false)
  const snapshot = useRef<RuntimeSubagent | null>(null)
  const revision = useRef(0)
  const request = useRef<AbortController | null>(null)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; revision.current++; request.current?.abort() } }, [])
  const load = useCallback(async (background = false) => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    const current = ++revision.current
    if (!background || !snapshot.current) setLoading(true)
    try {
      const response = await api.subagentDetail(owner, id, controller.signal)
      if (current !== revision.current || controller.signal.aborted) return
      if (response.ok && response.data.agent.agent_id === id) {
        snapshot.current = response.data.agent
        setData(response.data.agent)
        setError('')
        const child = response.data.agent.child_session_id || contextSessionId
        if (embedded && child) {
          try {
            const transcript = await api.getSession(owner, 'conversation-window', controller.signal, id)
            if (current !== revision.current || controller.signal.aborted) return
            if (transcript.ok && transcript.data.header.id === child) {
              setConversation(blocksFromDetail(transcript.data))
              setRecent(Boolean(transcript.data.history?.has_more))
              setConversationError(false)
            } else { setConversationError(true); return }
          } catch {
            if (current === revision.current && !controller.signal.aborted) setConversationError(true)
            return
          }
        }
        return response.data.agent
      }
      setError(response.ok ? {key: 'ui.team.member.identityMismatch'} : response.copy ?? response.message)
    } catch (failure) {
      if (current === revision.current && !controller.signal.aborted) setError(failureText(failure))
    } finally {
      if (current === revision.current && !controller.signal.aborted) setLoading(false)
    }
  }, [owner, id, contextSessionId, embedded])
  useEffect(() => {
    if (!embedded || !active || !pageVisible) return
    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let failures = 0
    const tick = async () => {
      const agent = await load(true)
      failures = agent ? 0 : failures + 1
      if (live && (!agent || ['pending', 'running'].includes(agent.status))) timer = setTimeout(tick, agent ? 2000 : Math.min(30000, 2000 * 2 ** Math.min(failures - 1, 4)))
    }
    void tick()
    return () => { live = false; clearTimeout(timer); revision.current++; request.current?.abort() }
  }, [embedded, active, pageVisible, load])
  const stopMember = async () => {
    if (stopLock.current || !data || !['pending', 'running'].includes(data.status)) return
    stopLock.current = true
    setStopping(true); setControlError('')
    try {
      const response = await api.stopSubagent(owner, id)
      if (!mounted.current) return
      if (!response.ok) { setControlError(response.copy ?? response.message); return }
      if (response.data.agent.agent_id !== id) { setControlError({key: 'ui.team.member.identityMismatch'}); return }
      request.current?.abort(); revision.current++
      snapshot.current = response.data.agent
      setData(response.data.agent); setLoading(false)
      if (observing.current) await load(true)
      if (mounted.current) setControlError(response.data.audit_error != null ? {key: 'ui.audit.agentStopIncomplete'} : '')
    } catch (failure) {
      if (mounted.current) setControlError(failureText(failure))
    } finally {
      stopLock.current = false
      if (mounted.current) setStopping(false)
    }
  }
  // 以运行明细里的子会话身份为准，历史快照仅在缺失时回退。
  const traceSession = data?.child_session_id || contextSessionId || ''
  const livePreview = Boolean(preview?.live && (preview.text || preview.thinking))
  const savedBlocks: Block[] = conversation?.length ? conversation : [
    ...(data?.prompt ? [{ id: 'saved-task', kind: 'user' as const, at: data.created_at, text: data.prompt }] : []),
    ...(data?.result && !livePreview ? [{ id: 'saved-result', kind: 'assistant' as const, at: data.created_at, text: data.result }] : []),
  ]
  const replyBlocks: Block[] = livePreview && preview?.text && !savedBlocks.some(block => block.kind === 'assistant' && block.text === preview.text)
    ? [...savedBlocks, { id: 'live-member-preview', kind: 'assistant', at: 0, text: preview.text, streaming: true }] : savedBlocks
  const conversationBlocks: Block[] = livePreview && preview?.thinking
    ? [...replyBlocks.filter(block => block.id !== 'live-member-preview' && !(block.kind === 'thinking' && (block.streaming || block.text === preview.thinking))),
      { id: 'live-member-thinking', kind: 'thinking', at: 0, text: preview.thinking, streaming: !preview.text }, ...replyBlocks.filter(block => block.id === 'live-member-preview')] : replyBlocks
  return <div className="min-w-0 space-y-2">
    {!embedded && <Button size="xs" aria-expanded={open} onClick={() => {
      if (open) { revision.current++; request.current?.abort(); setLoading(false); setTracesOpen(false) }
      else void load()
      setOpen(!open)
    }}>{t('ui.team.member.open', {name})}</Button>}
    {open && <section aria-label={t('ui.team.member.open', {name})} aria-busy={loading} className={embedded ? 'min-w-0 space-y-3' : 'min-w-0 space-y-2 rounded-lg border border-line bg-canvas/50 p-2'}>
      {data && <div role="region" aria-label={t('ui.team.member.summary')} className={embedded ? 'flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted' : 'flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-line bg-raised/40 px-3 py-2 text-xs text-muted'}>
        <Badge tone={data.status === 'completed' ? 'ok' : data.status === 'failed' ? 'danger' : data.status === 'interrupted' ? 'warn' : data.status === 'running' ? 'brand' : 'neutral'}>{t('ui.member.status.' + (data.status === 'completed' ? 'done' : data.status))}</Badge>
        {data.duration_ms != null && <span className="tabular-nums">{t('ui.team.member.duration', { duration: formatDuration(data.duration_ms) })}</span>}
        <span className="tabular-nums">{t(data.tool_calls === 1 ? 'ui.runtime.oneToolCall' : 'ui.runtime.toolCalls', { count: data.tool_calls })}</span>
        {data.pending_messages > 0 && <span className="tabular-nums text-warn">{t(data.pending_messages === 1 ? 'ui.runtime.onePendingMessage' : 'ui.runtime.pendingMessages', { count: data.pending_messages })}</span>}
        {embedded && ['pending', 'running'].includes(data.status) && <Button size="xs" variant="secondary" disabled={stopping} onClick={() => void stopMember()}>{t(stopping ? 'ui.team.member.stopping' : 'ui.team.member.stop')}</Button>}
        {embedded && <RefreshButton size="xs" variant="ghost" className="ml-auto" label={t('ui.team.member.refresh')} disabled={loading} onClick={() => void load()} />}
      </div>}
      {(!embedded || !data) && <RefreshButton size="xs" label={t('ui.team.member.refresh')} disabled={loading} onClick={() => void load()} />}
      {loading && <p role="status" className="text-2xs text-muted">{t('ui.team.member.loading')}</p>}
      {data?.archive_error && <p role="alert" className="break-words text-xs text-warn">{noticeText(data.archive_error_copy ?? data.archive_error)}</p>}
      {controlError && <p role="alert" className="break-words text-xs text-danger">{noticeText(controlError)}</p>}
      {error && <p role="alert" className="break-words text-xs text-danger">{data ? t('ui.team.member.cached', {error: noticeText(error)}) : noticeText(error)}</p>}
      {embedded && <>{conversationError && <p role="alert" className="text-xs text-warn">{t('ui.member.conversationFallback')}</p>}{recent && <p className="text-2xs text-muted">{t('ui.member.recentConversation')}</p>}<MemberConversation blocks={conversationBlocks} name={name} agent={agent} role={role} leadName={leadName} leadAgent={leadAgent} sessionId={traceSession || owner} resultScope={{ sessionId: owner, agentId: id, active: active && open }} />{data?.result_truncated && <p className="text-xs text-warn">{t('ui.team.member.resultTruncated')}</p>}{data?.error && <p className="text-xs text-danger">{agentErrorText(data)}</p>}</>}
      {!embedded && preview && preview.text && (!data?.result || livePreview) ? <details open><summary className="cursor-pointer text-2xs text-muted">{t('ui.team.member.latest')}</summary>
        {preview.truncated && <p className="mt-1 text-2xs text-warn">{t('ui.team.preview.truncated')}</p>}
        <pre className={embedded ? 'mt-2 whitespace-pre-wrap break-words text-sm' : 'mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-words text-2xs'}>{preview.text}</pre>
      </details> : null}
      {!embedded && data && <><details><summary className="cursor-pointer text-2xs text-muted">{t('ui.team.member.prompt')}</summary><pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-all text-2xs">{data.prompt}</pre></details>
        {!livePreview && data.result_truncated && <p className="text-xs text-warn">{t('ui.team.member.resultTruncated')}</p>}
        {!livePreview && <div className={embedded ? 'min-w-0 text-sm [overflow-wrap:anywhere]' : 'max-h-96 min-w-0 overflow-auto text-xs [overflow-wrap:anywhere]'}>{data.result ? <Markdown dense>{data.result}</Markdown> : <p className="text-muted">{t('ui.team.member.empty')}</p>}</div>}
        {data.error && <p className="break-words text-xs text-danger">{agentErrorText(data)}</p>}
      </>}
      {traceSession ? <div className="min-w-0 space-y-2 border-t border-current/10 pt-2">
        <Button size="xs" aria-expanded={tracesOpen} onClick={() => setTracesOpen(value => !value)}>{t(tracesOpen ? 'ui.team.member.traces.hide' : 'ui.team.member.traces.show')}</Button>
        {tracesOpen && <div className="min-w-0">
          <DeferredPanel load={loadRequests} panelProps={{ sessionId: owner, agentSessionId: traceSession, active: active && pageVisible && open }} label={t('ui.team.member.traces')} />
        </div>}
      </div> : null}
    </section>}
  </div>
}
