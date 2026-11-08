import {observeSessionCompletion} from '@/store/sessionActivity'
import {prepareTaskNotifications} from '@/store/sessionNotifications'
import {submissionErrorText} from '@/lib/submissionFeedback'
import {applyLiveGoalCommand} from '@/lib/liveGoalCommand'
import {failureText} from '@/lib/resourceError'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  AttachmentInfo, StreamEvent, SessionDetail } from '@/types'
import { api } from '@/lib/api'
import { SessionSocket } from '@/lib/ws'
import { defaultReasoningBlocked, recoverProjectCatalog, useAppStore } from '@/store/app'
import { useChatStore } from '@/store/chat'
import type { TeamAddress } from '@/lib/agentMount'
import { useDraftStore } from '@/store/drafts'
import { runtimeRequest } from '@/lib/http'
import type { SubmissionStatus, SteeringStatus } from '@/lib/protocol.generated'
import type { SessionPermissionState } from '@/lib/protocol.generated'
import { usePermissionStore } from '@/store/permissions'
import { useStopStore } from '@/store/stops'
import { usePlanModeStore } from '@/store/planModes'

/** 服务端 ready 提供权威快照和流草稿；缺口及重启均重新恢复快照。
 * 断线采用序号续传和指数退避，只接受当前服务端的权威快照。
 */

export interface SessionChannel {
  send: (text: string, attachments?: AttachmentInfo[], teamAddress?: TeamAddress) => boolean | Promise<boolean>
  cancel: () => void
  steer: (text: string, attachments?: AttachmentInfo[]) => boolean
  clearQueue: () => void
  togglePlan: (active: boolean) => void
  reconnect: () => void
  refreshHistory: () => void
  connection: string
}

export function useSessionSocket(sessionId: string | null, readOnly = false): SessionChannel {
  const [visible, setVisible] = useState(() => !document.hidden)
  useEffect(() => {
    if (!readOnly) return
    const changed = () => setVisible(!document.hidden)
    document.addEventListener('visibilitychange', changed)
    return () => document.removeEventListener('visibilitychange', changed)
  }, [readOnly])
  const socketRef = useRef<SessionSocket | null>(null)
  const ready = useRef(false)
  const refreshQuestions = useRef<((queue?: boolean) => void) | null>(null)
  const apply = useChatStore((s) => s.apply)
  const hydrate = useChatStore((s) => s.hydrate)
  const setConnection = useChatStore((s) => s.setConnection)
  const setQuestions = useChatStore((s) => s.setQuestions)
  const appendLocalUser = useChatStore((s) => s.appendLocalUser)
  const toast = useAppStore((s) => s.toast)
  const refreshSessions = useAppStore((s) => s.refreshSessions)
  const connection = useChatStore((s) => (sessionId ? s.sessions[sessionId]?.connection : 'idle'))

  /* ---------- 快照 + 连接 ---------- */
  useEffect(() => {
    if (!sessionId || readOnly && !visible) return
    let cancelled = false
    let recovering = false
    let recoveryTimer: ReturnType<typeof setTimeout> | null = null
    ready.current = false
    let questionAbort = new AbortController()
    let questionReading = false
    let questionQueued = false
    let questionRevision = 0
    const readQuestions = (queue = false) => {
      if (cancelled || readOnly) return
      // 事件使旧快照失效，定时器只在没有读取时补查，不饿死慢请求。
      if (queue) ++questionRevision
      if (questionReading) { questionQueued ||= queue; return }
      questionReading = true
      const ticket = ++questionRevision
      void api.pendingQuestions(sessionId, questionAbort.signal).then(result => {
        if (!cancelled && ticket === questionRevision && result.ok) setQuestions(sessionId, result.data.questions ?? [])
      }).catch(() => undefined).finally(() => {
        questionReading = false
        if (questionQueued && !cancelled) {
          questionQueued = false
          readQuestions()
        }
      })
    }
    refreshQuestions.current = readQuestions
    const visibilityChanged = () => {
      if (readOnly) return
      if (document.hidden) {
        ++questionRevision
        questionAbort.abort()
        questionQueued = false
        return
      }
      questionAbort = new AbortController()
      readQuestions(true)
    }
    document.addEventListener('visibilitychange', visibilityChanged)
    const store = useChatStore.getState()
    store.markLoading(sessionId, true)

    const socket = new SessionSocket({
      sessionId,
      snapshotView: 'conversation-window',
      getSince: () => useChatStore.getState().get(sessionId).lastSeq,
      onState: (state, detail) => {
        setConnection(sessionId, state, detail)
        if (!readOnly && (state === 'reconnecting' || state === 'closed')) useDraftStore.getState().markUnknown(sessionId)
      },
      onEvent: (event: StreamEvent) => {
        if (cancelled) return
        const type = (event as { type?: string }).type
        if (!readOnly && (type === 'settings/changed' || type === 'ready')) void useAppStore.getState().refreshLanguage()
        const raw = event as unknown as Record<string, unknown>
        const current = useChatStore.getState().get(sessionId)
        if ((type === 'submission/accepted' || type === 'steer/accepted') && typeof raw.request_id === 'string') {
          if (type === 'steer/accepted' && raw.receipt) apply(sessionId, { type: 'steer/state', receipt: raw.receipt })
          useDraftStore.getState().settleSubmission(sessionId, raw.request_id)
          return
        }
        if (type === 'error' && raw.submitted === false && typeof raw.request_id === 'string') {
          useDraftStore.getState().settleSubmission(sessionId, raw.request_id, submissionErrorText(raw))
          socket.send({ action: 'resume', since: current.lastSeq, force_snapshot: true })
          return
        }
        if (type !== 'ready' && Number(raw.seq ?? 0) > current.lastSeq + 1 && current.serverId) {
          if (!recovering) {
            recovering = true
            if (!socket.send({ action: 'resume', since: current.lastSeq, force_snapshot: true })) {
              recovering = false
            } else {
              recoveryTimer = setTimeout(() => { recovering = false; recoveryTimer = null }, 5000)
            }
          }
          return
        }
        if (type === 'ready') {
          if (!readOnly) void recoverProjectCatalog()
          ready.current = true
          if (recoveryTimer !== null) clearTimeout(recoveryTimer)
          recoveryTimer = null
          recovering = false
          if (raw.snapshot) {
            const detail = raw.snapshot as SessionDetail
            hydrate(sessionId, detail)
            if (Array.isArray(detail.questions)) setQuestions(sessionId, detail.questions)
          }
        }
        if (readOnly) {
          apply(sessionId, event)
          return
        }
        if (type === 'ready') {
          const permissions = (raw.snapshot as SessionDetail | undefined)?.permissions
          if (permissions) usePermissionStore.getState().receive(permissions)
          for (const item of useDraftStore.getState().submissions[sessionId] ?? []) {
            if (item.status === 'rejected') continue
            if (item.sendOnReady) {
              const sent = socket.send({ action: 'send', request_id: item.id, text: item.text, attachments: item.attachments,
                recipients: item.teamAddress?.members, recipient_team: item.teamAddress?.team, recipient_identities: item.teamAddress?.identities, selection_revision: item.teamAddress?.selectionRevision })
              if (sent && useChatStore.getState().get(sessionId).blocks.length === 0) appendLocalUser(sessionId, item.text, item.attachments)
              continue
            }
            useDraftStore.getState().markUnknown(sessionId)
            if (item.action === 'steer') {
              void runtimeRequest<SteeringStatus>('/sessions/' + sessionId + '/steering/' + encodeURIComponent(item.id)).then((result) => {
                if (cancelled || !result.receipt) return
                apply(sessionId, { type: 'steer/state', receipt: result.receipt })
                if (result.receipt.status === 'unapplied') useDraftStore.getState().markUnapplied(sessionId, item.id, result.receipt.reason)
                else useDraftStore.getState().settleSubmission(sessionId, item.id)
              }).catch(() => undefined)
              continue
            }
            if (item.action !== 'send') continue
            void runtimeRequest<SubmissionStatus>('/sessions/' + sessionId + '/submissions/' + encodeURIComponent(item.id)).then((result) => {
              if (cancelled || useAppStore.getState().activeSessionId !== sessionId) return
              if (result.run) useDraftStore.getState().settleSubmission(sessionId, item.id)
            }).catch(() => undefined)
          }
        }
        if (type === 'permission_policy' && Number(raw.seq) > current.lastSeq &&
            (!current.serverId || raw.server_id === current.serverId)) {
          usePermissionStore.getState().receive(raw.permissions as SessionPermissionState)
        }
        // 交互类事件需要回查 REST：问题被回答后要让卡片落成"已回答"。
        if (type === 'question_answered' || type === 'ready' || type?.startsWith('user_question/')) {
          readQuestions(true)
        }
        if (type === 'turn/end' || type === 'done') {
          void refreshSessions()
        }
        if (type === 'browser/activity' || type === 'ready' || type === 'turn/end' || type === 'done') {
          window.dispatchEvent(new CustomEvent('harness-browser-activity', {detail:{...raw, session_id:sessionId}}))
        }
        if (type === 'browser/activity' && !document.hidden && (raw.status === 'waiting_dialog' ||
            raw.actor === 'agent' && raw.status === 'running' && (raw.reveal === true || !['observe', 'tabs', 'screenshot'].includes(String(raw.action))))) {
          useAppStore.getState().setInspectorTab('browser')
        }
        apply(sessionId, event)
        if (raw.server_id === current.serverId && typeof raw.seq === 'number' && raw.seq > current.lastSeq) observeSessionCompletion(sessionId, raw)
        if (type === 'ready' || type === 'turn/start' || type === 'turn/end' || type === 'done' || type === 'run/state') {
          const activity = useChatStore.getState().get(sessionId)
          useStopStore.getState().observe(sessionId, activity.running, activity.turn)
        }
      },
    })
    socketRef.current = socket
    socket.connect()
    const restored = () => socket.reset()
    window.addEventListener('harness-service-restored', restored)

    return () => {
      cancelled = true
      questionAbort.abort()
      if (recoveryTimer !== null) clearTimeout(recoveryTimer)
      document.removeEventListener('visibilitychange', visibilityChanged)
      if (refreshQuestions.current === readQuestions) refreshQuestions.current = null
      window.removeEventListener('harness-service-restored', restored)
      socket.close()
      socketRef.current = null
      ready.current = false
      useChatStore.getState().markLoading(sessionId, false)
    }
  }, [sessionId, readOnly, visible, apply, hydrate, setConnection, setQuestions, refreshSessions, toast, appendLocalUser])

  /* ---------- 提问轮询兜底 ----------
     交互请求是服务端主动发起的，不一定有 WS 事件伴随；
     有活动时才轮询，空闲会话不做无谓请求。 */
  useEffect(() => {
    if (!sessionId || readOnly) return
    let stopped = false
    let timer: ReturnType<typeof setInterval> | null = null
    const tick = () => {
      if (stopped || document.hidden) return
      const state = useChatStore.getState().get(sessionId)
      if (!state.hydrated || (!state.running && !state.queueRunning && !state.queued && !state.questions.length)) return
      refreshQuestions.current?.()
    }
    const visibilityChanged = () => {
      if (timer !== null) { clearInterval(timer); timer = null }
      if (!document.hidden && !stopped) {
        void tick()
        timer = setInterval(tick, 4000)
      }
    }
    visibilityChanged()
    document.addEventListener('visibilitychange', visibilityChanged)
    return () => {
      stopped = true
      if (timer !== null) clearInterval(timer)
      document.removeEventListener('visibilitychange', visibilityChanged)
    }
  }, [sessionId, readOnly, setQuestions])

  /* ---------- 动作 ---------- */
  const send = useCallback(
    async (text: string, attachments?: AttachmentInfo[], teamAddress?: TeamAddress) => {
      if (!sessionId || readOnly) return false
      if (defaultReasoningBlocked(sessionId)) {
        toast({title: {key: 'ui.models.reasoningBeforeSend'}, tone: 'danger'})
        return false
      }
      const executionMode = useDraftStore.getState().bySession[sessionId]?.executionMode
      if (!ready.current) {
        toast({ title: {key: 'ui.hooks.useSessionSocket.dfba1899c6d4'}, description: {key: 'ui.hooks.useSessionSocket.693dab70d76d'}, tone: 'info' })
        return false
      }
      if (/^\/goal(?:\s|$)/.test(text.trim()) && useChatStore.getState().get(sessionId).running && !teamAddress && !attachments?.length) {
        try {
          const state = await applyLiveGoalCommand(sessionId, text)
          apply(sessionId, {type: 'goal/state', ...state})
          if (executionMode === 'goal') useDraftStore.getState().consumeExecutionMode(sessionId, 'goal')
          return true
        } catch (failure) {
          toast({title: {key: 'ui.components.chat.GoalDock.825cfa17d9cd'}, description: failureText(failure), tone: 'danger'})
          return false
        }
      }
      prepareTaskNotifications()
      const requestId = crypto.randomUUID()
      useDraftStore.getState().rememberSubmission(sessionId, { id: requestId, text, attachments: attachments ?? [], action: 'send', status: 'sending', teamAddress })
      // 先本地回显：用户不该等一个网络往返才看到自己说的话。
      // 附件跟着**这条消息**一起发。服务端会把它们物化进提示词，
      // 所以这里必须发的是 id 而不是路径 —— 路径是服务端的实现细节。
      const ok =
        socketRef.current?.send({
          action: 'send',
          request_id: requestId,
          text,
          recipients: teamAddress?.members,
          recipient_team: teamAddress?.team,
          selection_revision: teamAddress?.selectionRevision,
          recipient_identities: teamAddress?.identities,
          attachments: attachments && attachments.length > 0 ? attachments : undefined,
        }) ?? false
      if (!ok) {
        useDraftStore.getState().settleSubmission(sessionId, requestId)
        toast({ title: {key: 'ui.hooks.useSessionSocket.ac77953a1e06'}, description: {key: 'ui.hooks.useSessionSocket.e1e257086621'}, tone: 'warn' })
      }
      if (ok && (executionMode === 'plan' || executionMode === 'goal')) useDraftStore.getState().consumeExecutionMode(sessionId, executionMode)
      const current = useChatStore.getState().get(sessionId)
      if (ok && !current.running && !current.queuePaused) appendLocalUser(sessionId, text, attachments)
      void refreshSessions()
      return ok
    },
    [sessionId, readOnly, appendLocalUser, refreshSessions, toast, apply],
  )

  const cancel = useCallback(() => {
    if (!sessionId || readOnly) return
    const activity = useChatStore.getState().get(sessionId)
    if (!activity.running || activity.stopping) return
    const socket = socketRef.current
    void useStopStore.getState().stop(sessionId, activity.turn, () => {
      if (socketRef.current !== socket) return
      if (!socket?.send({ action: 'resume', since: 0, force_snapshot: true })) socket?.reset()
    })
  }, [sessionId, readOnly])

  const steer = useCallback((text: string, attachments?: AttachmentInfo[]) => {
    if (!sessionId || readOnly) return false
    if (!ready.current) return false
    const turn = useChatStore.getState().get(sessionId).turn
    const requestId = crypto.randomUUID()
    useDraftStore.getState().rememberSubmission(sessionId, { id: requestId, text, attachments: attachments ?? [], action: 'steer', status: 'sending' })
    const accepted = socketRef.current?.send({ action: 'steer', text, attachments, expected_turn: turn, request_id: requestId }) ?? false
    if (!accepted) useDraftStore.getState().settleSubmission(sessionId, requestId)
    return accepted
  }, [sessionId, readOnly])

  const clearQueue = useCallback(() => {
    if (!readOnly) socketRef.current?.send({ action: 'clear_queue' })
  }, [readOnly])

  const togglePlan = useCallback(
    (active: boolean) => {
      if (!sessionId || readOnly) return
      void usePlanModeStore.getState().change(sessionId, active)
    },
    [sessionId, readOnly],
  )

  const reconnect = useCallback(() => {
    socketRef.current?.reset()
  }, [])

  const refreshHistory = useCallback(() => {
    const socket = socketRef.current
    if (!socket?.send({ action: 'resume', since: 0, force_snapshot: true })) socket?.reset()
  }, [])

  return useMemo(
    () => ({ send, steer, cancel, clearQueue, togglePlan, reconnect, refreshHistory, connection: connection ?? 'idle' }),
    [send, steer, cancel, clearQueue, togglePlan, reconnect, refreshHistory, connection],
  )
}
