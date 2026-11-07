import {t} from '@/i18n'
import {voiceRequest} from './voiceApi'
import {readTextPage} from './textPage'
import {attachmentErrorCopy} from './attachmentErrors'
import type { ExternalAgentCatalog } from './externalAgents'
import type { RequestTrace, RequestTraceDetail } from './requestTraces'
import type { ContextAudit, SkillAuditPage } from './contextAudit'
import type {
  AttachmentInfo,
  AttachmentStats,
  DirectoryListing,
  ModelCatalog,
  PendingQuestion,
  PlanModeState,
  RuntimePayload,
  RuntimeJob,
  RuntimeTerminal,
  SubagentDetailPayload,
  JobOutputPayload,
  SessionDetail,
  SessionSummary,
  SkillInfo,
  StatusPayload,
  TerminalTailPayload,
  TerminalStopPayload,
  ConversationSearchResult,
  ConversationMessagePart,
  ToolSchema,
  UiSettings,
  WorkspaceInfo,
  WorkspacePayload,
} from '@/types'

import { request, readRequest } from './http'
import type { ApiFailure } from './http'
export { ApiError, unwrap } from './http'
export type { ApiFailure, ApiResult } from './http'

import type {McpTunnelSettings, McpServiceState} from './protocol.generated'
export type {McpServiceState} from './protocol.generated'

export const mcpServiceStatus = (signal?: AbortSignal) => request<McpServiceState>('/api/v1/mcp-service', { signal })
export const mcpServiceStart = async (tunnel?: McpTunnelSettings) => {
  const result = await request<McpServiceState>('/api/v1/mcp-service/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({scope: 'all', ...(tunnel ? {tunnel} : {})}) })
  if (!result.ok && result.code === 'mcp/tunnel-config' && result.details && typeof result.details === 'object') {
    const copy = (result.details as {copy?: {key?: unknown; params?: unknown}}).copy
    const keys = ['ui.mcpService.error.tunnelConfig', 'ui.mcpService.error.tunnelConfigFile', 'ui.mcpService.error.cleanupPending', 'ui.mcpService.error.configChanged']
    if (copy && typeof copy.key === 'string' && keys.includes(copy.key) && copy.params && typeof copy.params === 'object' && !Array.isArray(copy.params) && Object.keys(copy.params).length === 0) return {...result, copy: {key: copy.key}}
  }
  return result
}
export const mcpServiceStop = () => request<McpServiceState>('/api/v1/mcp-service/stop', { method: 'POST', timeout: 30_000 })
export const mcpServicePassword = () => request<{ password: string }>('/api/v1/mcp-service/password', { method: 'POST' })
export const mcpCompanionConfigure = (settings: {enabled: boolean; port: number}) => request<McpServiceState>('/api/v1/mcp-service/companion', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(settings)})
export const mcpCompanionToken = () => request<{token: string}>('/api/v1/mcp-service/companion/token', {method: 'POST'})

const get = <T,>(path: string) => request<T>(path)
type CatalogReadOptions = {retry?: boolean}
const catalogGet = <T,>(path: string, options?: CatalogReadOptions) => options?.retry ? readRequest<T>(path) : get<T>(path)
const post = <T,>(path: string, body?: unknown, init?: RequestInit & { timeout?: number }) =>
  request<T>(path, {
    ...init,
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
const patchMethod = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
const del = <T,>(path: string) => request<T>(path, { method: 'DELETE' })

let dictationOpening: ReturnType<typeof post<import('./protocol.generated').VoiceDictationOpen>> | null = null
function openVoiceDictation() {
  // 页面关闭重开仍共用已接受的打开请求，不重复唤起原生应用。
  return dictationOpening ??= post<import('./protocol.generated').VoiceDictationOpen>('/api/v1/voice/dictation/open', {}, {timeout: 20_000})
    .finally(() => {dictationOpening = null})
}

export const api = {
  voiceDictation: (signal?: AbortSignal) => voiceRequest<import('./protocol.generated').VoiceDictationCapabilities>('/api/v1/voice/dictation', {signal}),
  openVoiceDictation,
  accessStatus: () => request<import('./protocol.generated').BrowserAccessStatus>('/api/v1/access/status'),
  accessLogin: (token: string) => request<import('./protocol.generated').BrowserAccessSession>('/api/v1/access/login', {method: 'POST', body: JSON.stringify({token})}),
  accessLogout: () => request<import('./protocol.generated').BrowserAccessLogout>('/api/v1/access/logout', {method: 'POST', body: '{}'}),
  externalAgents: (signal?: AbortSignal) => request<ExternalAgentCatalog>('/api/v1/external-agents', { signal }),
  screenshotCapabilities: () => get<import('./protocol.generated').ScreenshotCapabilities>('/api/v1/screenshot/capabilities'),
  screenshot: (hideWindow: boolean, windowToken: string) =>
    post<{ cancelled: true } | { cancelled: false; data: string }>('/api/v1/screenshot', { hideWindow, windowToken }, { timeout: 155_000 }),
  /* 状态 */
  status: (options?: CatalogReadOptions) => catalogGet<StatusPayload>('/api/v1/status', options),
  tools: (options?: CatalogReadOptions) => catalogGet<{ tools: ToolSchema[] }>('/api/v1/tools', options),
  skills: (options?: CatalogReadOptions) => catalogGet<{ skills: SkillInfo[] }>('/api/v1/skills', options),
  models: (options?: CatalogReadOptions) => catalogGet<ModelCatalog>('/api/v1/models', options),

  contextAudit: (id: string, through?: number, before?: number, signal?: AbortSignal, agentId?: string) =>
    request<ContextAudit>('/api/v1/sessions/' + encodeURIComponent(id) + '/context?' + new URLSearchParams({
      ...(through === undefined ? {} : { through: String(through) }), ...(before === undefined ? {} : { before: String(before) }),
      ...(agentId ? { agent_id: agentId } : {}),
    }), { signal, timeout: 35_000 }),
  toolResult: (id: string, ref: import('./protocol.generated').ToolResultReference, offset: number, query?: string, signal?: AbortSignal, agentId?: string) =>
    request<import('./protocol.generated').ToolResultPart>('/api/v1/sessions/' + encodeURIComponent(id) + '/tool-results/' + ref.event_seq + '?' + new URLSearchParams({
      index: String(ref.result_index), offset: String(offset), sha256: ref.sha256,
      ...(query ? { q: query } : {}), ...(agentId ? { agent_id: agentId } : {}),
    }), { signal, timeout: 35_000 }),
  skillAudit: (id: string, through: number, before?: number, signal?: AbortSignal, agentId?: string) =>
    request<SkillAuditPage>('/api/v1/sessions/' + encodeURIComponent(id) + '/context/skills?' + new URLSearchParams({
      through: String(through), ...(before === undefined ? {} : { before: String(before) }), ...(agentId ? { agent_id: agentId } : {}),
    }), { signal, timeout: 35_000 }),
  skillOriginal: (id: string, seq: number, through: number, offset: number, signal?: AbortSignal, agentId?: string) =>
    readTextPage('/api/v1/sessions/' + encodeURIComponent(id) + '/context/skills/' + seq + '?' + new URLSearchParams({
      through: String(through), offset: String(offset), ...(agentId ? { agent_id: agentId } : {}),
    }), offset, signal, 35_000),
  compactionPart: (id: string, startSeq: number, through: number, part: string, offset = 0, signal?: AbortSignal, agentId?: string) =>
    readTextPage('/api/v1/sessions/' + encodeURIComponent(id) + '/context/compactions/' + startSeq + '?' +
      new URLSearchParams({ through: String(through), part, offset: String(offset), ...(agentId ? { agent_id: agentId } : {}) }), offset, signal, 35_000),
  executionTrace: (id: string, agentSession?: string, after?: string, signal?: AbortSignal) =>
    request<import('./executionTrace').ExecutionTracePage>('/api/v1/sessions/' + encodeURIComponent(id) + '/trace?' + new URLSearchParams({
      ...(agentSession ? { agent_session: agentSession } : {}), ...(after ? { after } : {}),
    }), { signal, timeout: 35_000 }),
  executionTracePart: (id: string, entry: string, part: string, offset: number, agentSession?: string, signal?: AbortSignal) =>
    readTextPage('/api/v1/sessions/' + encodeURIComponent(id) + '/trace?' + new URLSearchParams({
      entry, part, offset: String(offset), ...(agentSession ? { agent_session: agentSession } : {}),
    }), offset, signal, 35_000),
  requestTrace: async (id: string, traceId: string, signal?: AbortSignal, summary = false) => {
    const result = await request<RequestTraceDetail>('/api/v1/sessions/' + encodeURIComponent(id) + '/requests/' + encodeURIComponent(traceId) + (summary ? '?part=summary' : ''), { signal, timeout: 35_000 })
    // 概要没有额外用户参数；明确拒绝时提示核对运行版本，保留原始诊断。
    if (summary && !result.ok && result.status === 400 && ['bad-request', 'request-trace/unsupported-part'].includes(result.code)) {
      const copy = {key: 'ui.requestComposition.summaryRejected'}
      return {...result, copy, message: t(copy.key), details: {original: result.message, details: result.details}}
    }
    return result
  },
  requestTraceStatus: async (id: string, traceId: string, signal?: AbortSignal) => {
    const result = await request<RequestTrace>('/api/v1/sessions/' + encodeURIComponent(id) + '/requests/' + encodeURIComponent(traceId) + '/status', { signal, timeout: 35_000 })
    if (!result.ok || result.data?.id === traceId) return result
    return {ok: false as const, status: 200, code: 'response/invalid-schema', message: t('ui.http.invalidResponse'), copy: {key: 'ui.http.invalidResponse'}}
  },
  requestTracePart: (id: string, traceId: string, part: string, index = 0, offset = 0, signal?: AbortSignal) =>
    readTextPage('/api/v1/sessions/' + encodeURIComponent(id) + '/requests/' + encodeURIComponent(traceId) + '?part=' + part + '&index=' + index + '&offset=' + offset, offset, signal, 35_000),
  requestTraceExport: (id: string, traceId: string) =>
    '/api/v1/sessions/' + encodeURIComponent(id) + '/requests/' + encodeURIComponent(traceId) + '/export',

  /* 会话 */
  listSessions: (options?: CatalogReadOptions) => catalogGet<{ sessions: SessionSummary[] }>('/api/v1/sessions', options),
  searchConversation: (id: string, query: string, options: { signal?: AbortSignal; through?: number; before?: number } = {}) => {
    const params = new URLSearchParams({ q: query })
    if (options.through !== undefined) params.set('through', String(options.through))
    if (options.before !== undefined) params.set('before', String(options.before))
    return request<ConversationSearchResult>('/api/v1/sessions/' + encodeURIComponent(id) + '/search?' + params, { signal: options.signal })
  },
  readConversationMessage: (id: string, seq: number, start = 0, signal?: AbortSignal) =>
    request<ConversationMessagePart>('/api/v1/sessions/' + encodeURIComponent(id) + '/messages/' + seq + '?start=' + start, { signal }),
  sessionActivity: (signal?: AbortSignal, cursor?: {serverId: string; after: number}) =>
    request<import('./protocol.generated').SessionActivity>('/api/v1/sessions/activity' +
      (cursor ? '?' + new URLSearchParams({server_id: cursor.serverId, after: String(cursor.after)}) : ''), {signal}),
  createSession: (body: import('./protocol.generated').NewConversationRequest = {}, requestId?: string) =>
    post<{ session: SessionSummary; agent_mount?: import('@/lib/agentMount').AgentMountState }>('/api/v1/sessions', body,
      requestId ? {headers: {'X-Request-ID': requestId}} : undefined),
  getSession: (id: string, view?: 'conversation' | 'conversation-window', signal?: AbortSignal, agentId?: string) => request<SessionDetail>('/api/v1/sessions/' + encodeURIComponent(id) + '?' + new URLSearchParams({ ...(view ? { view } : {}), ...(agentId ? { agent_id: agentId } : {}) }), { signal }),
  deleteSession: (id: string) => del<{ deleted: string }>('/api/v1/sessions/' + encodeURIComponent(id)),
  updateSession: (id: string, body: import('./protocol.generated').ConversationPatch) =>
    patchMethod<{ session: SessionSummary }>('/api/v1/sessions/' + encodeURIComponent(id), body),
  exportUrl: (id: string) => '/api/v1/sessions/' + encodeURIComponent(id) + '/export',

  goal: (id: string) => get<import('@/types').GoalState>('/api/v1/sessions/' + encodeURIComponent(id) + '/goal'),
  controlGoal: (id: string, body: { action: string; goal_id?: string; source_seq?: number; revision?: number; objective?: string; max_goal_rounds?: number | null; success_criteria?: string[] | null; token_budget?: number | null }) =>
    post<import('@/types').GoalState>('/api/v1/sessions/' + encodeURIComponent(id) + '/goal', body),

  goalEvidence: (id: string, goalId: string, seq: number, offset = 0, signal?: AbortSignal) =>
    request<{ evidence: import('@/types').GoalEvidencePage }>('/api/v1/sessions/' + encodeURIComponent(id) + '/goal/' + encodeURIComponent(goalId) + '/evidence/' + seq + '?offset=' + offset, { signal }),

  /* 运行时设置 */
  settings: (options?: CatalogReadOptions) => catalogGet<{ settings: UiSettings }>('/api/v1/ui-settings', options),
  updateSettings: (body: Partial<UiSettings>) =>
    patchMethod<{ settings: UiSettings; rejected: string[] }>('/api/v1/ui-settings', body),

  /* 目录浏览（项目选择器） */
  listDirectories: (path?: string, signal?: AbortSignal) =>
    request<DirectoryListing>('/api/v1/directories' + (path ? '?path=' + encodeURIComponent(path) : ''), {signal}),

  /* 交互 */
  pendingQuestions: (sessionId?: string, signal?: AbortSignal) =>
    request<{ questions: PendingQuestion[] }>(
      '/api/v1/questions' + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : ''), {signal},
    ),
  answerQuestion: (questionId: string, answers: unknown) =>
    post<Record<string, unknown>>('/api/v1/questions/answer', { question_id: questionId, answers }),
  planMode: (sessionId: string) =>
    get<{ plan_mode: PlanModeState }>('/api/v1/plan-mode?session_id=' + encodeURIComponent(sessionId)),
  setPlanMode: (sessionId: string, active: boolean) =>
    post<PlanModeState>('/api/v1/plan-mode', { session_id: sessionId, active }),

  /**
   * 运行时可观测快照：常驻终端 / 子代理 / 后台作业。
   *
   * 不传 sessionId 就是全局视图（设置面板总览用），传了就只看该会话。
   * 轮询频率由调用方定 —— 这些状态每次都会变，没有 ETag 可言。
   */
  runtime: (sessionId?: string | null, signal?: AbortSignal) =>
    request<RuntimePayload>(
      '/api/v1/runtime' + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : ''),
      { signal },
    ),
  terminalTypes: (signal?: AbortSignal) =>
    request<import('./protocol.generated').TerminalTypes>('/api/v1/terminal/types', { signal }),
  terminals: (sessionId: string, signal?: AbortSignal) =>
    request<{ terminals: RuntimeTerminal[]; backends: string[]; default_type: string; enabled: boolean; managed_runtime: boolean }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/terminals`, { signal }),
  openTerminal: (sessionId: string, type: string) =>
    post<{ terminal: RuntimeTerminal; audit_error?: string }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/terminals`, { type }),
  terminalStream: (sessionId: string, terminalId: string, cursor: number, signal?: AbortSignal) =>
    request<{ text: string; cursor: number; start: number; end: number; reset: boolean; eof: boolean; terminal: RuntimeTerminal }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/terminals/${encodeURIComponent(terminalId)}/stream?cursor=${cursor}`, { signal }),
  terminalInput: (sessionId: string, terminalId: string, text: string, submit = false) =>
    post<{ terminal: RuntimeTerminal; audit_error?: string }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/terminals/${encodeURIComponent(terminalId)}/input`, { text, submit }),
  resizeTerminal: (sessionId: string, terminalId: string, cols: number, rows: number) =>
    post<{ terminal: RuntimeTerminal }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/terminals/${encodeURIComponent(terminalId)}/resize`, { cols, rows }),
  jobOutput: (sessionId: string, jobId: string, signal?: AbortSignal) =>
    request<JobOutputPayload>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/jobs/${encodeURIComponent(jobId)}/output`, { signal }),
  jobHistory: (sessionId: string, before: string, signal?: AbortSignal) =>
    request<{ jobs: RuntimeJob[]; has_more: boolean; next_before: string | null }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/jobs?before=${encodeURIComponent(before)}`, { signal }),
  stopJob: (sessionId: string, jobId: string) =>
    post<{ outcome: string; job: RuntimeJob; audit_error?: string }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/jobs/${encodeURIComponent(jobId)}/stop`, {}),
  subagentDetail: (sessionId: string, agentId: string, signal?: AbortSignal) =>
    request<SubagentDetailPayload>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/subagents/${encodeURIComponent(agentId)}`, { signal }),
  subagentHistory: (sessionId: string, before: string, signal?: AbortSignal) =>
    request<import('@/lib/protocol.generated').SubagentPage>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/subagents?before=${encodeURIComponent(before)}`, { signal }),
  stopSubagent: (sessionId: string, agentId: string) =>
    post<SubagentDetailPayload>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/subagents/${encodeURIComponent(agentId)}/stop`, {}),
  terminalTail: (sessionId: string | null, terminalId: string, signal?: AbortSignal) =>
    request<TerminalTailPayload>(sessionId
      ? `/api/v1/sessions/${encodeURIComponent(sessionId)}/terminals/${encodeURIComponent(terminalId)}/output`
      : `/api/v1/runtime/terminals/${encodeURIComponent(terminalId)}/output?tail=true`, { signal }),
  stopTerminal: (sessionId: string, terminalId: string) =>
    post<TerminalStopPayload>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/terminals/${encodeURIComponent(terminalId)}/stop`, {}),

  /* 项目（Workspace）：会话之上的目录分组层 */
  listWorkspaces: (options?: CatalogReadOptions) => catalogGet<{ workspaces: WorkspaceInfo[]; default: string }>('/api/v1/workspaces', options),
  registerWorkspace: (body: { path: string; name?: string; color?: string }) =>
    post<{ workspace: WorkspaceInfo }>('/api/v1/workspaces', body),
  mkdirWorkspace: (parent: string, name: string) =>
    post<{ created: string; name: string }>('/api/v1/workspaces/mkdir', { parent, name }),
  updateWorkspace: (id: string, body: { name?: string; color?: string }) =>
    patchMethod<{ workspace: WorkspaceInfo }>('/api/v1/workspaces/' + encodeURIComponent(id), body),
  unregisterWorkspace: (id: string) => del<{ deleted: string }>('/api/v1/workspaces/' + encodeURIComponent(id)),

  /* 目录浏览 */
  workspace: (path?: string, root?: string | null, options?: {offset?: number; query?: string; signal?: AbortSignal; retry?: boolean}) => {
    const qs = new URLSearchParams()
    if (path) qs.set('path', path)
    if (root) qs.set('root', root)
    if (options?.offset) qs.set('offset', String(options.offset))
    if (options?.query) qs.set('q', options.query)
    const suffix = qs.toString() ? '?' + qs.toString() : ''
    return (options?.retry ? readRequest<WorkspacePayload> : request<WorkspacePayload>)('/api/v1/workspace' + suffix, {signal: options?.signal})
  },
  openFile: (path: string, root: string | null, action: 'default' | 'terminal' | 'folder') =>
    post<{ requested: boolean; action: string }>('/api/v1/file/open', { path, root, action }),

  /**
   * 上传附件。
   *
   * 超时给得比默认长：32 MiB 的文件在慢速上传时要几十秒，用默认的 20s
   * 会让一次成功的上传在界面上报成"失败"。
   */
  uploadAttachments: async (files: { name: string; mediaType: string; data: string }[]) => {
    const result = await post<{ attachments: AttachmentInfo[]; stats: AttachmentStats }>('/api/v1/attachments', { files }, {
      timeout: 180_000,
    })
    if (result.ok) return result
    const copy = attachmentErrorCopy(result.code, result.details)
    return copy === undefined ? result : copy === null
      ? {...result, code: 'response/invalid-schema', copy: {key: 'ui.http.invalidResponse'}}
      : {...result, copy}
  },

  /** 附件字节。内容寻址 = 不可变，所以这条 URL 会被浏览器长期缓存。 */
  attachmentUrl: (id: string) => '/api/v1/attachments/' + encodeURIComponent(id),
}

/** 一次性抓齐目录类数据；单项失败降级，不阻塞首屏。 */
export async function loadCatalogs() {
  const [status, tools, skills, workspace] = await Promise.all([
    api.status({retry: true}),
    api.tools({retry: true}),
    api.skills({retry: true}),
    api.workspace(undefined, undefined, {retry: true}),
  ])
  const failed = [status, tools, skills, workspace].filter((r) => r.ok === false)
  return {
    status: status.ok ? status.data : null,
    tools: tools.ok ? tools.data.tools ?? [] : [],
    skills: skills.ok ? skills.data.skills ?? [] : [],
    workspace: workspace.ok ? workspace.data : null,
    errors: failed.map((r) => (r as ApiFailure).message),
  }
}
