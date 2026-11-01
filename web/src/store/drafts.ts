import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { validPersistedDrafts } from '@/lib/draftPersistence'
import { createBrowserDraftStorage } from '@/lib/browserDraftStorage'
import { clearDrafts, nextLocalId, type DraftAttachment } from '@/lib/attachments'
import { api } from '@/lib/api'
import type { AttachmentInfo, ResourceCopy, ResourceText } from '@/types'
import type { TeamAddress } from '@/lib/agentMount'
import { useQueueEditStore } from './queueEdits'
import { useChatStore } from './chat'

export interface PendingSubmission {
  executionMode?: SessionDraft['executionMode']
  agentSelection?: PersistentAgentSelection
  sendOnReady?: boolean
  id: string
  text: string
  attachments: AttachmentInfo[]
  action: 'send' | 'steer'
  status: 'sending' | 'unknown' | 'rejected' | 'unapplied'
  error?: ResourceText
  teamAddress?: TeamAddress
}

export interface PersistentAgentSelection {name: string; display_name?: string; agent_type: 'agent' | 'team'}
export interface GoalCreationDraft { objective: string; rounds: string; budget: string }

export interface SessionCreationRequest {id: string; cwd?: string; agent?: string; project?: import('@/lib/protocol.generated').ConversationProject}

interface SessionDraft {
  creationRequest?: SessionCreationRequest
  goalCreation?: GoalCreationDraft
  inlineEdits?: Record<string, string>
  executionMode?: import('@/lib/composerMode').ComposerMode
  agentSelection?: PersistentAgentSelection
  teamAddress?: TeamAddress
  text: string
  attachments: DraftAttachment[]
  editOrigin?: { sessionId: string; sourceSeq: number }
}
interface DraftState {
  setCreationRequest: (request?: SessionCreationRequest) => void
  setGoalCreation: (sessionId: string, draft?: GoalCreationDraft) => void
  bySession: Record<string, SessionDraft>
  storageError: ResourceCopy | null
  submissions: Record<string, PendingSubmission[]>
  rememberSubmission: (sessionId: string, submission: PendingSubmission) => void
  settleSubmission: (sessionId: string, id: string, error?: ResourceText) => void
  markUnknown: (sessionId: string) => void
  markUnapplied: (sessionId: string, id: string, reason: ResourceText) => void
  restoreSubmission: (sessionId: string, id: string) => boolean
  restoreQueuedText: (sessionId: string, text: string, attachments?: AttachmentInfo[]) => void
  setInlineEdit: (sessionId: string, key: string, text?: string) => void
  setEditOrigin: (sessionId: string, origin?: { sessionId: string; sourceSeq: number }) => void
  setExecutionMode: (sessionId: string, mode?: import('@/lib/composerMode').ComposerMode) => void
  consumeExecutionMode: (sessionId: string, expected: 'plan' | 'goal') => void
  setText: (sessionId: string, text: string) => void
  updateAttachments: (sessionId: string, update: (current: DraftAttachment[]) => DraftAttachment[]) => void
  setAgentSelection: (sessionId: string, selection?: PersistentAgentSelection) => void
  setTeamAddress: (sessionId: string, address?: TeamAddress) => void
  discard: (sessionId: string) => void
}

let failureReported: 'invalid' | 'unavailable' | null = null
function storageFailure(reason?: 'invalid') {
  const kind = reason ?? 'unavailable'
  if (failureReported === kind || failureReported === 'invalid') return
  failureReported = kind
  queueMicrotask(() => useDraftStore.setState({ storageError: {key: reason === 'invalid' ? 'ui.drafts.invalidStorage' : 'ui.store.drafts.f9e99a9805ac'} }))
}
/** 隔离无效持久快照，不把旧字段迁移为当前草稿或重放提交。 */
function resolveDraftStorage(): Storage | undefined {
  if (typeof localStorage === 'undefined') return undefined
  const key = 'harness.drafts.v1'
  const raw = localStorage.getItem(key)
  if (raw !== null && !validPersistedDrafts(raw)) {
    try {
      const backup = `${key}.invalid.${crypto.randomUUID()}`
      localStorage.setItem(backup, raw)
      if (localStorage.getItem(backup) !== raw || localStorage.getItem(key) !== raw) return localStorage
      localStorage.removeItem(key)
    } catch { /* 隔离失败保留原件，由当前格式校验继续阻止覆盖。 */ }
  }
  return localStorage
}
const storage = createJSONStorage(() => createBrowserDraftStorage(
  resolveDraftStorage, storageFailure, validPersistedDrafts,
))

export const useDraftStore = create<DraftState>()(persist((set, get) => ({
  bySession: {},
  setCreationRequest: (creationRequest) => set(state => ({bySession: {...state.bySession,
    __unsaved__: {...state.bySession.__unsaved__, text: state.bySession.__unsaved__?.text ?? '', attachments: state.bySession.__unsaved__?.attachments ?? [], creationRequest},
  }})),
  storageError: null,
  submissions: {},
  setGoalCreation: (sessionId, goalCreation) => set(state => ({bySession: {...state.bySession,
    [sessionId]: {...state.bySession[sessionId], text: state.bySession[sessionId]?.text ?? '', attachments: state.bySession[sessionId]?.attachments ?? [], goalCreation},
  }})),
  rememberSubmission: (sessionId, submission) => set((state) => ({ submissions: { ...state.submissions, [sessionId]: [...(state.submissions[sessionId] ?? []), { executionMode: state.bySession[sessionId]?.executionMode, agentSelection: state.bySession[sessionId]?.agentSelection, ...submission }] } })),
  settleSubmission: (sessionId, id, error) => set((state) => ({ submissions: { ...state.submissions, [sessionId]: error !== undefined
    ? (state.submissions[sessionId] ?? []).map((item) => item.id === id ? { ...item, status: 'rejected', error } : item)
    : (state.submissions[sessionId] ?? []).filter((item) => item.id !== id) } })),
  markUnknown: (sessionId) => set((state) => ({ submissions: { ...state.submissions, [sessionId]: (state.submissions[sessionId] ?? []).map((item) => item.status === 'sending' ? { ...item, status: 'unknown' } : item) } })),
  markUnapplied: (sessionId, id, reason) => set((state) => ({ submissions: { ...state.submissions,
    [sessionId]: (state.submissions[sessionId] ?? []).map((item) => item.id === id ? { ...item, status: 'unapplied', error: reason } : item) } })),
  restoreSubmission: (sessionId, id) => {
    const state = get()
    const draft = state.bySession[sessionId]
    const item = state.submissions[sessionId]?.find((entry) => entry.id === id)
    if (!item || draft?.text || draft?.attachments.length) return false
    set({ bySession: { ...state.bySession, [sessionId]: { ...draft, executionMode: item.executionMode ?? draft?.executionMode, agentSelection: item.agentSelection ?? draft?.agentSelection, text: item.text, teamAddress: item.teamAddress ?? draft?.teamAddress, attachments: item.attachments.map((attachment) => ({ ...attachment, localId: 'recovered-' + attachment.attachmentId, previewUrl: attachment.isImage ? api.attachmentUrl(attachment.attachmentId) : undefined })) } } })
    get().settleSubmission(sessionId, id)
    return true
  },
  restoreQueuedText: (sessionId, text, attachments = []) => set((state) => {
    const draft = state.bySession[sessionId]
    return { bySession: { ...state.bySession, [sessionId]: {
      ...draft,
      text: [draft?.text, text].filter(Boolean).join('\n\n'),
      attachments: [...(draft?.attachments ?? []), ...attachments.map(attachment => ({ ...attachment, localId: nextLocalId(), previewUrl: attachment.isImage ? api.attachmentUrl(attachment.attachmentId) : undefined }))],
    } } }
  }),
  setInlineEdit: (sessionId, key, text) => set(state => {
    const draft = state.bySession[sessionId]
    const inlineEdits = {...draft?.inlineEdits}
    if (text === undefined) delete inlineEdits[key]
    else inlineEdits[key] = text
    return {bySession: {...state.bySession, [sessionId]: {...draft, text: draft?.text ?? '', attachments: draft?.attachments ?? [], inlineEdits}}}
  }),
  setEditOrigin: (sessionId, editOrigin) => set((state) => ({ bySession: { ...state.bySession,
    [sessionId]: { ...state.bySession[sessionId], text: state.bySession[sessionId]?.text ?? '', attachments: state.bySession[sessionId]?.attachments ?? [], editOrigin },
  } })),
  setExecutionMode: (sessionId, executionMode) => set(state => ({bySession: {...state.bySession, [sessionId]: {...state.bySession[sessionId], text: state.bySession[sessionId]?.text ?? '', attachments: state.bySession[sessionId]?.attachments ?? [], executionMode}}})),
  consumeExecutionMode: (sessionId, expected) => {
    if (get().bySession[sessionId]?.executionMode === expected) get().setExecutionMode(sessionId)
  },
  setText: (sessionId, text) => set((state) => ({ bySession: {
    ...state.bySession,
    [sessionId]: { ...state.bySession[sessionId], text, attachments: state.bySession[sessionId]?.attachments ?? [] },
  } })),
  updateAttachments: (sessionId, update) => set((state) => ({ bySession: {
    ...state.bySession,
    [sessionId]: { ...state.bySession[sessionId], text: state.bySession[sessionId]?.text ?? '', attachments: update(state.bySession[sessionId]?.attachments ?? []) },
  } })),
  setAgentSelection: (sessionId, agentSelection) => set(state => ({bySession: {...state.bySession, [sessionId]: {...state.bySession[sessionId], text: state.bySession[sessionId]?.text ?? '', attachments: state.bySession[sessionId]?.attachments ?? [], agentSelection}}})),
  setTeamAddress: (sessionId, teamAddress) => set(state => ({ bySession: { ...state.bySession, [sessionId]: { ...state.bySession[sessionId], text: state.bySession[sessionId]?.text ?? '', attachments: state.bySession[sessionId]?.attachments ?? [], teamAddress } } })),
  discard: (sessionId) => set((state) => {
    useQueueEditStore.getState().setEdit(sessionId, null)
    useQueueEditStore.getState().setWithdrawal(sessionId, null)
    clearDrafts(state.bySession[sessionId]?.attachments ?? [])
    const next = { ...state.bySession }
    delete next[sessionId]
    const submissions = { ...state.submissions }
    delete submissions[sessionId]
    return { bySession: next, submissions }
  }),
}), {
  name: 'harness.drafts.v1',
  storage,
  partialize: (state) => ({ submissions: state.submissions, bySession: Object.fromEntries(Object.entries(state.bySession).map(([id, draft]) => [id, {
    text: draft.text,
    creationRequest: draft.creationRequest,
    editOrigin: draft.editOrigin,
    inlineEdits: draft.inlineEdits,
    teamAddress: draft.teamAddress,
    agentSelection: draft.agentSelection,
    executionMode: draft.executionMode,
    goalCreation: draft.goalCreation,
    attachments: draft.attachments.map((item) => item.attachmentId && !item.status
      ? { ...item, previewUrl: item.isImage ? api.attachmentUrl(item.attachmentId) : undefined }
      : { ...item, previewUrl: undefined, status: 'error', error: item.status === 'error' && item.error ? item.error : {key: "ui.store.drafts.5dc58adf4cac"} }),
  }])) }),
}))

export const EMPTY_ATTACHMENTS: DraftAttachment[] = []
export const EMPTY_SUBMISSIONS: PendingSubmission[] = []

// 按服务端确认的计划退出和目标终态消费对应选择，保留正文及后选模式。
useChatStore.subscribe((state, previous) => {
  for (const [id, session] of Object.entries(state.sessions)) {
    const before = Object.hasOwn(previous.sessions, id) ? previous.sessions[id] : undefined
    if ((before?.planModeState.active || before?.planMode)
      && !session.planModeState.active && session.planModeState.pending === null
      && session.planModeRevision !== before?.planModeRevision) {
      useDraftStore.getState().consumeExecutionMode(id, 'plan')
    }
    if (before?.goalState.goal && before.goalState.goal.phase !== 'complete'
      && (!session.goalState.goal || session.goalState.goal.phase === 'complete')
      && session.goalState !== before.goalState) {
      useDraftStore.getState().consumeExecutionMode(id, 'goal')
    }
  }
})
