import {readRequester} from '@/lib/sharedAccess'
import { readAgentSpeaker, type AgentSpeaker } from '@/lib/agentSpeaker'
import type { UserBlock } from '@/types'
import { create } from 'zustand'
import { readTeamAuthor, readTeamRecipients } from '@/lib/teamAuthor'
import { readMemberStream, readTeamProgress } from '@/lib/teamProgress'
import { mergeAgentMount, type AgentMountState } from '@/lib/agentMount'
import { mergeToolOutput } from '@/lib/toolOutput'
import { readHookActivity, mergeHookActivity, settleHookActivity } from '@/lib/hookActivity'
import type { HookActivity } from '@/types'
import { generationDelta, readGeneration, updateGeneration, type GenerationState } from '@/lib/generation'
import { mergeSteering, readTeamDelivery } from '@/lib/steering'
import type {
  ResourceText,
  ApprovalNotice,
  ToolBlockState,
  TeamBlock,
  AttachmentInfo,
  Block,
  ConnectionState,
  PendingQuestion,
  PlanModeState,
  SessionDetail,
  SessionPrompt,
  StreamEvent,
  TodoItem,
  TokenUsage,
  ToolUseInfo,
} from '@/types'
import { matchesLocalEcho } from '@/lib/attachments'
import { uid } from '@/lib/utils'
import { presentedArtifacts } from '@/lib/presentedArtifacts'
import { outcomeBuckets, runErrorCopy, sameTurnFailure, turnOutcome } from '@/lib/turnOutcome'
import { mergeTurnActivity, type TurnActivity } from '@/lib/turnActivity'
import { runtimeRequest, ApiError } from '@/lib/http'
import { readPlanModeState, effectivePlanMode, type ConfirmedPlanMode } from '@/lib/planModeState'
import type { ConversationHistory } from '@/types'

export interface LoadedHistory extends ConversationHistory {
  cursorThrough: number
  generation: number
  revision: number
  loading: boolean
  error: ResourceText | null
  resetRequired: boolean
  ids: string[]
}

/**
 * 会话实时状态。
 *
 * 核心是 `applyEvent` —— 把后端事件流折叠成一串可渲染的 block。
 * 折叠而不是原样透传，是因为 UI 需要的是"结构"：
 * 几千条 `assistant/delta` 拼成一条消息、N 个 `tool/start|end` 配对成一张卡片、
 * 多个 `subagent/status` 收敛成一行进度。做这个收敛的地方只有一处，
 * 组件就只剩纯渲染。
 */

export interface Draft {
  speaker?: AgentSpeaker
  id?: string
  at?: number
  text: string
  thinking: string
  turn?: number
}

export interface ChatSessionState {
  contextRevision: number
  turnActivity: Record<number, TurnActivity>
  goalState: import("@/types").GoalState
  steering: import('@/lib/protocol.generated').SteeringReceipt[]
  generation: GenerationState | null
  history: LoadedHistory | null
  blocks: Block[]
  draft: Draft
  running: boolean
  stopping: boolean
  turn: number
  queued: number
  queueRunning: boolean
  queuePaused: boolean
  queuePauseReason: string
  queueWaitingReason: '' | 'environment'
  queueControlAt: number | null
  serverId: string | null
  lastSeq: number
  usage: TokenUsage
  todos: TodoItem[]
  questions: PendingQuestion[]
  /** 本次会话生效的系统/开发者提示词，只读，不进对话。 */
  prompts: SessionPrompt[]
  agentMount: AgentMountState | null
  planMode: boolean
  planModeState: ConfirmedPlanMode
  planModeRevision: number
  planRevision: number
  connection: ConnectionState
  attempt: number
  retryIn: number
  loading: boolean
  hydrated: boolean
  error: ResourceText | null
  /** agent_id -> block id。 */
  agentIndex: Record<string, string>
  /** 团队 run_id -> block id。 */
  teamIndex: Record<string, string>
  subagents: number
  toolCalls: number
  toolStatsKnown: boolean
  toolErrors: number
}

export const emptySession = (): ChatSessionState => ({
  contextRevision: 0,
  turnActivity: {},
  goalState: { goal: null, activation: "disarmed", stateVersion: 0 },
  generation: null,
  history: null,
  steering: [],
  blocks: [],
  draft: { text: '', thinking: '' },
  running: false,
  stopping: false,
  turn: 0,
  queued: 0,
  queueRunning: false,
  queuePaused: false,
  queuePauseReason: '',
  queueWaitingReason: '',
  queueControlAt: null,
  serverId: null,
  lastSeq: 0,
  usage: { total_tokens: 0, input_tokens: 0, output_tokens: 0, requests: 0 },
  todos: [],
  questions: [],
  prompts: [],
  agentMount: null,
  planMode: false,
  planModeState: { active: false, pending: null },
  planModeRevision: 0,
  planRevision: 0,
  connection: 'idle',
  attempt: 0,
  retryIn: 0,
  loading: false,
  hydrated: false,
  error: null,
  agentIndex: {},
  teamIndex: {},
  subagents: 0,
  toolCalls: 0,
  toolStatsKnown: false,
  toolErrors: 0,
})

type Patch = Partial<ChatSessionState>

/* ==================================================================
   快照 -> block
   ================================================================== */

/**导出供单测：快照 -> block 的纯函数转换。 */
export function blocksFromDetail(detail: SessionDetail): Block[] {
  const blocks: Block[] = []
  const messages = detail.messages ?? []
  const windowed = detail.events_view === 'conversation-window'
  const stable = (kind: string, seq: number | undefined, suffix = '') => windowed && seq != null ? `history:${kind}:${seq}:${suffix}` : uid(kind)
  const outcomes = outcomeBuckets(messages, detail.events ?? [])
  const pushOutcomes = (index: number) => { for (const outcome of outcomes.get(index) ?? []) blocks.push(outcome) }
  // 轮次与调用 ID 共同标识原始工具调用输入。
  // 快照里 ToolResultMessage 只有结果，没有 input；但助手消息带着 tool_uses。
  // 不回填的话，刷新之后折叠态的文件 chip 和 diff 视图的路径/词级 diff 全丢。
  const callKey = (turn: unknown, id: string) => JSON.stringify([typeof turn === 'number' ? turn : null, id])
  const inputsById = new Map<string, ToolUseInfo>()
  const decisions = new Map<string, Omit<ApprovalNotice, 'tool'>>()
  const progressById = new Map<string, NonNullable<import('@/types').ToolOutputPreview>>()
  const started = new Map<string, { data: Record<string, unknown>; seq?: number; time?: number }>()
  const resolved = new Map<string, number>()
  const hooksById = new Map<string, HookActivity[]>()
  const teams = new Map<string, Extract<Block, { kind: 'team' }>>()
  const stopChecks = new Map<string, { row: HookActivity; at: number }>()
  let eventTurn: unknown
  for (const event of detail.events ?? []) {
    const data = event.data as Record<string, unknown> | undefined
    if (event.type === 'turn/start') eventTurn = data?.turn
    const key = callKey(data?.turn ?? eventTurn, String(data?.call_id ?? ''))
    if (event.type === 'agent/team' && data) {
      const previous = typeof data.run_id === 'string' ? teams.get(data.run_id) : undefined
      const row = readTeamProgress({ ...data, source_seq:event.seq, source_time:event.time }, previous)
      if (row) teams.set(row.runId, row)
    }
    if (event.type === 'tool/call' && data?.status === 'running') {
      started.set(key, { data: { ...data, turn: data.turn ?? eventTurn }, seq: event.seq, time: event.time })
      hooksById.set(key, [])
    }
    if (event.type === 'note' && data?.kind === 'hook_run') {
      const row = readHookActivity({ ...data, source_seq: event.seq })
      if (row?.event === 'Stop' && row.session_id === detail.header.id) {
        const old = stopChecks.get(row.id)
        if (!old || old.row.source_seq < row.source_seq) stopChecks.set(row.id, { row, at: typeof event.time === 'number' && Number.isFinite(event.time) ? event.time : Date.now() })
        continue
      }
      if (row && row.session_id === detail.header.id) hooksById.set(callKey(row.turn, row.call_id), mergeHookActivity(hooksById.get(callKey(row.turn, row.call_id)), row))
    }
    if (event.type === 'tool/output' && data?.call_id) {
      const progress = mergeToolOutput(progressById.get(key), data)
      if (progress) progressById.set(key, progress)
    }
    if (event.type === 'note' && data?.kind === 'permission_decision' && Array.isArray(data.decision)) {
      decisions.set(key, { decisions: data.decision, reviewer: data.reviewer, reason: data.reason })
    }
    if (event.type === 'turn/end') eventTurn = undefined
  }
  for (const [index, message] of messages.entries()) {
    pushOutcomes(index)
    const at = typeof message.source_time === 'number' && Number.isFinite(message.source_time) ? message.source_time : Date.now()
    const sourceSeq = message.source_seq
    const turn = typeof message.turn === 'number' && Number.isSafeInteger(message.turn) && message.turn > 0 ? message.turn : undefined
    if (message.role === 'user' && message.source === 'compaction') {
      blocks.push({ id: 'compaction:' + (message.source_seq ?? index), kind: 'compaction', at, sourceSeq, phase: 'end', summary: message.text })
      continue
    }
    if (message.role === 'user' && message.source === 'goal') {
      blocks.push({ id: stable('goal-round', sourceSeq), kind: 'notice', at, sourceSeq, turn, text: { key: "ui.store.chat.64f873358223" }, tone: 'info' })
      continue
    }
    if (message.role === 'user') {
      const text = message.text ?? ''
      if (text.trim() || message.attachments?.length) {
        const delivery = readTeamDelivery(message.steering_team_delivered, message.steering_delivery_seq, sourceSeq)
        blocks.push({ id: stable('user', sourceSeq), kind: 'user', at, sourceSeq, turn, text: text, attachments: message.attachments, goalId: message.goal_id,
          requester: readRequester(message.requester), teamRecipients: readTeamRecipients(message.team_recipients),
          requestId: typeof message.request_id === 'string' ? message.request_id : undefined,
          steeringStatus: message.steering_status as UserBlock['steeringStatus'],
          steeringTeamDelivered: delivery.team_delivered, steeringDeliverySeq: delivery.delivery_seq,
          steeringReason: typeof message.steering_reason === 'string' ? message.steering_reason : undefined })
      }
      continue
    }
    // 系统与开发者提示词属于配置，仅在提示词面板呈现，不生成对话气泡。
    if (message.role === 'system' || message.role === 'developer') continue
    if (message.role === 'assistant') {
      for (const call of (message.tool_uses ?? []) as ToolUseInfo[]) {
        const id = String(call?.id ?? call?.call_id ?? '')
        if (id) inputsById.set(callKey(turn, id), call)
      }
      const partial = message.partial === 'stopped' || message.partial === 'failed' || message.partial === 'interrupted' ? message.partial : undefined
      if (message.thinking && message.thinking.trim()) {
        blocks.push({
          id: stable('thinking', sourceSeq), sourceSeq, turn,
          kind: 'thinking',
          speaker: readAgentSpeaker(message.speaker),
          at,
          text: message.thinking,
          streaming: false,
          partial,
          checkpointGap: message.checkpoint_gap === true,
        })
      }
      const text = message.text ?? ''
      const calls = (message.tool_uses ?? []) as ToolUseInfo[]
      if (text.trim() || (calls.length === 0 && !partial)) {
        blocks.push({
          id: stable('assistant', sourceSeq), sourceSeq, turn,
          kind: 'assistant',
          author: readTeamAuthor(message.author),
          speaker: readAgentSpeaker(message.speaker),
          error: message.error === true,
          at,
          text,
          streaming: false,
          usage: message.usage ?? null,
          model: message.model ?? null,
          partial,
          checkpointGap: message.checkpoint_gap === true,
        })
      }
      // tool_calls 本身不单独成块——结果在下面的 tool 结果里。
      continue
    }
    if (message.role === 'tool') {
      for (const [resultIndex, result] of (message.results ?? []).entries()) {
        const callId = String(result.tool_use_id ?? result.call_id ?? uid('c'))
        const resultTurn = typeof result.turn === 'number' ? result.turn : turn
        const key = callKey(resultTurn, callId)
        resolved.set(key, message.source_seq ?? Infinity)
        const call = inputsById.get(key)
        blocks.push({
          id: stable('tool', windowed ? result.call_source_seq ?? sourceSeq : undefined, `${callId}:${resultIndex}`), sourceSeq,
          kind: 'tool',
          turn: resultTurn,
          at,
          callId,
          name: String(windowed ? result.name ?? call?.name ?? 'tool' : call?.name ?? result.name ?? 'tool'),
          status: result.is_error ? 'error' : 'ok',
          input: windowed ? result.input ?? call?.input : call?.input,
          output: result.content ?? result.display,
          contentRef: result.content_ref,
          display: result.display,
          isError: Boolean(result.is_error),
          durationMs: typeof result.duration_ms === 'number' ? result.duration_ms : undefined,
          hooks: (result.hooks ?? []).map(readHookActivity).filter((row): row is HookActivity => Boolean(row && row.event !== 'Stop' && row.call_id === callId && row.turn === resultTurn && row.session_id === detail.header.id)),
          progress: windowed ? result.progress ?? progressById.get(key) : progressById.get(key),
        })
        const decision = windowed && result.approval_decision ? { decisions: result.approval_decision, reviewer: result.approval_reviewer, reason: result.approval_reason } : decisions.get(key)
        const tool = blocks[blocks.length - 1]!
        if (tool.kind === 'tool') {
          const artifacts = presentedArtifacts(tool.name, Boolean(result.is_error), result.display, tool.id, at, sourceSeq)
          if (artifacts) blocks.push(artifacts)
        }
        if (decision) blocks.push({ id: stable('approval', sourceSeq, String(resultIndex)), kind: 'notice', at: windowed ? at : Date.now(), sourceSeq, text: { approval: { ...decision, tool: String(windowed ? result.name ?? call?.name ?? 'tool' : call?.name ?? result.name ?? 'tool') } }, tone: 'info' })
      }
    }
  }
  // 运行时刷新也必须恢复正在执行的工具；未实际启动的排队调用不冒充执行中。
  for (const [key, { data, seq, time }] of started) {
    const callId = String(data.call_id)
    if ((resolved.get(key) ?? -1) >= (seq ?? 0)) continue
    const call = inputsById.get(key)
    blocks.push({ id: stable('tool', windowed ? seq : undefined, `${callId}:0`), kind: 'tool', at: windowed ? time ?? Date.now() : Date.now(), sourceSeq: windowed ? seq : undefined, callId,
      name: String(call?.name ?? data.name ?? 'tool'), input: call?.input ?? data.input,
      turn: typeof data.turn === 'number' ? data.turn : undefined,
      status: detail.interrupted ? 'error' : 'running', progress: progressById.get(key),
      hooks: detail.interrupted ? settleHookActivity(hooksById.get(key)) : hooksById.get(key),
      outputKey: detail.interrupted ? "ui.store.chat.eda14e9f81aa" : undefined })
  }
  pushOutcomes(messages.length)
  for (const { row, at } of stopChecks.values()) {
    const hook: Block = { kind: 'hook', id: `hook:${row.id}`, sourceSeq: row.source_seq, turn: row.turn, at,
      hooks: detail.interrupted ? settleHookActivity([row])! : [row] }
    blocks.push(hook)
  }
  blocks.push(...teams.values())
  if (windowed || stopChecks.size > 0 || teams.size > 0) blocks.sort((a, b) => (a.sourceSeq ?? Infinity) - (b.sourceSeq ?? Infinity))
  return blocks
}

function restoredToolStats(detail: SessionDetail, blocks: Block[]) {
  const calls = detail.stats?.tool_calls, errors = detail.stats?.tool_errors
  if (Number.isSafeInteger(calls) && Number(calls) >= 0 && Number.isSafeInteger(errors) && Number(errors) >= 0 && Number(errors) <= Number(calls)) {
    return { toolCalls: Number(calls), toolErrors: Number(errors), toolStatsKnown: true }
  }
  // A partial snapshot cannot prove whole-session totals. Label this
  // fallback as loaded records instead of silently publishing a total of zero.
  const tools = blocks.filter(block => block.kind === 'tool')
  return { toolCalls: tools.length, toolErrors: tools.filter(block => block.status === 'error').length, toolStatsKnown: false }
}

function sumUsage(a: TokenUsage, b: TokenUsage | null | undefined): TokenUsage | null {
  if (b === null || b === undefined) return a
  const keys = [
    'input_tokens',
    'output_tokens',
    'cache_read_tokens',
    'cache_reported_input_tokens',
    'reasoning_tokens',
  ] as const
  if (typeof b !== 'object' || Array.isArray(b)) return null
  const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
  const out: TokenUsage = { ...a }
  for (const key of [...keys, 'requests'] as const) {
    const delta = b[key] === undefined ? key === 'requests' ? 1 : 0 : b[key]
    const previous = a[key] === undefined ? 0 : a[key]
    if (!count(delta) || !count(previous) || !count(previous + delta)) return null
    out[key] = previous + delta
  }
  const total = (out.input_tokens ?? 0) + (out.output_tokens ?? 0)
  if (!count(total)) return null
  out.total_tokens = total
  return out
}

function settleOpenTools(blocks: Block[]): Block[] {
  return blocks.map((block) => block.kind === 'hook' ? { ...block, hooks: settleHookActivity(block.hooks)! } : block.kind === 'tool' && block.status === 'running'
    ? { ...block, status: 'error', isError: true, outcomePending: true,
        hooks: settleHookActivity(block.hooks),
        outputKey: block.output == null ? block.outputKey ?? "ui.store.chat.a57ff6bf2376" : undefined,
        display: { ...(block.display && typeof block.display === 'object' ? block.display : {}), interrupted: true } }
    : block)
}

function linkLoadedOutcomes(blocks: Block[]): Block[] {
  let lastUser: number | undefined
  return blocks.map((block) => {
    if (block.kind === 'user' && block.sourceSeq != null) lastUser = block.sourceSeq
    return (block.kind === 'error' || block.kind === 'turn' && block.phase === 'end') && block.sourceSeq != null && lastUser != null && lastUser < block.sourceSeq && block.historical
      ? { ...block, historical: false } : block
  })
}

/* ==================================================================
   事件折叠
   ================================================================== */

/** Keep the same row/Markdown/SVG DOM throughout streaming and finalization. */
export function displayBlocks(state: ChatSessionState): Block[] {
  const { draft } = state
  if (!draft.text && !draft.thinking) return state.blocks
  const out = state.blocks.slice(), id = draft.id ?? 'stream-draft', at = draft.at ?? 0
  const turn = draft.turn ?? (state.turn > 0 ? state.turn : undefined)
  if (draft.thinking) out.push({ id: id + '-thinking', kind: 'thinking', at, text: draft.thinking, streaming: true, turn, speaker: draft.speaker })
  if (draft.text) out.push({ id: id + '-text', kind: 'assistant', at, text: draft.text, streaming: true, turn, speaker: draft.speaker })
  // 当前模型尚未采用待处理引导；将其放在这段流式内容之后，
  // 避免最新用户指令被持续增长的草稿压到上方。
  let anchor = state.blocks.length
  while (anchor > 0) {
    const block = state.blocks[anchor - 1]!
    if (block.kind !== 'user' || block.steeringStatus !== 'pending') break
    anchor--
  }
  if (anchor < state.blocks.length) {
    const streaming = out.splice(state.blocks.length)
    out.splice(anchor, 0, ...streaming)
  }
  return out
}

function finalizeDraft(state: ChatSessionState, patch: Patch, partial?: 'stopped' | 'failed' | 'interrupted', sourceSeq?: number): Patch {
  const { draft } = state
  const blocks = state.blocks.slice()
  const at = draft.at ?? Date.now()
  // 当前轮次由已受理的 turn/start 确定；清空单次请求草稿不能丢失轮次归属。
  const turn = draft.turn ?? (state.turn > 0 ? state.turn : undefined)
  if (draft.thinking.trim()) {
    blocks.push({ id: draft.id ? draft.id + '-thinking' : uid('th'), kind: 'thinking', at, text: draft.thinking, streaming: false, partial, sourceSeq, turn, speaker: draft.speaker })
  }
  if (draft.text) {
    blocks.push({
      id: draft.id ? draft.id + '-text' : uid('a'),
      kind: 'assistant',
      at,
      text: draft.text,
      speaker: draft.speaker,
      streaming: false,
      turn,
      partial,
      sourceSeq,
    })
  }
  patch.blocks = blocks
  patch.draft = { text: '', thinking: '', turn }
  return patch
}

/**
 * 折叠一条事件。纯函数：只读 state，返回要合并的 patch。
 */
export function reduceEvent(state: ChatSessionState, event: StreamEvent): Patch {
  const patch: Patch = {}
  const raw = event as Record<string, unknown>

  if (event.type !== 'ready' && state.serverId && typeof raw.server_id === 'string' && raw.server_id !== state.serverId) return {}
  if (raw.seq !== undefined && (typeof raw.seq !== 'number' || !Number.isSafeInteger(raw.seq) || raw.seq < 0)) return {}
  const seq = typeof raw.seq === 'number' ? raw.seq : 0
  if (seq > 0 && seq <= state.lastSeq && event.type !== 'ready') return {}
  if (seq > state.lastSeq) patch.lastSeq = seq

  const activeRun = state.turnActivity[state.turn]?.runId
  const closingRun = event.type === 'run/state' ? raw.id : raw.run_id
  if ((event.type === 'run/state' || event.type === 'done') && activeRun && typeof closingRun === 'string' && closingRun !== activeRun) return patch

  if (['ready', 'request/start', 'request/end', 'turn/end', 'compaction/end', 'system/prompt', 'user/message'].includes(event.type)) {
    patch.contextRevision = (state.contextRevision ?? 0) + 1
  }
  const push = (block: Block) => {
    patch.blocks = state.blocks.concat([block])
  }

  switch (event.type) {
    case 'turn/start': {
      patch.turnActivity = mergeTurnActivity(state.turnActivity, [{ type: event.type, data: raw, time: typeof raw.time === 'number' ? raw.time : Date.now(), seq: typeof raw.source_seq === 'number' ? raw.source_seq : undefined }])
      patch.steering = []
      patch.generation = null
      patch.running = true
      patch.queueRunning = true
      patch.turn = Number(raw.turn ?? state.turn + 1)
      patch.error = null
      patch.draft = { text: '', thinking: '', turn: patch.turn }
      return patch
    }

    case 'user/message': {
      if (raw.source === 'goal') {
        push({ id: uid('goal-round'), kind: 'notice', at: Date.now(), turn: typeof raw.turn === 'number' ? raw.turn : state.turn, text: { key: "ui.store.chat.64f873358223" }, tone: 'info' })
        return patch
      }
      const prompt = String(raw.text ?? '')
      if (typeof raw.source_seq === 'number' && state.blocks.some(block => block.kind === 'user' && block.sourceSeq === raw.source_seq)) return patch
      const echoText = typeof raw.original_text === 'string' ? raw.original_text : prompt
      if (raw.source === 'steer' && typeof raw.request_id === 'string') {
        const receipt = state.steering.find(item => item.request_id === raw.request_id)
        const index = state.blocks.findIndex(block => block.kind === 'user' && block.requestId === raw.request_id)
        if (index >= 0) {
          patch.blocks = state.blocks.map((block, i) => i === index ? { ...block, steeringStatus: 'applied', steeringReason: '' } as Block : block)
          return patch
        }
        if (receipt) return { ...patch, ...reduceEvent(state, { type: 'steer/state', receipt: { ...receipt, text: prompt, text_truncated: false, status: 'applied', applied_seq: raw.source_seq } }) }
      }
      const index = state.blocks.findIndex(block => !raw.requester && block.kind === 'user' && block.sourceSeq === undefined
        && !block.requestId && matchesLocalEcho(block.text, echoText))
      const last = state.blocks[index]
      // 回显携带原始正文；附件内容只供模型使用，不按提示词前缀去重。
      if (raw.source !== 'steer' && last && last.kind === 'user' && last.sourceSeq === undefined) {
        // 对上后展示原始输入，保留本地附件引用；技能展开内容仍由后端保存用于模型。
        patch.blocks = state.blocks.slice()
        patch.blocks[index] = { ...last, goalId: typeof raw.goal_id === 'string' ? raw.goal_id : last.goalId, requester: readRequester(raw.requester), teamRecipients: readTeamRecipients(raw.team_recipients), text: echoText,
          sourceSeq: typeof raw.source_seq === 'number' ? raw.source_seq : last.sourceSeq,
          turn: typeof raw.turn === 'number' ? raw.turn : last.turn }
        return patch
      }
      push({ id: uid('u'), kind: 'user', at: typeof raw.source_time === 'number' ? raw.source_time : Date.now(),
        sourceSeq: typeof raw.source_seq === 'number' ? raw.source_seq : undefined, text: echoText,
        attachments: Array.isArray(raw.attachments) ? raw.attachments as AttachmentInfo[] : undefined,
        goalId: typeof raw.goal_id === 'string' ? raw.goal_id : undefined,
        requester: readRequester(raw.requester), teamRecipients: readTeamRecipients(raw.team_recipients),
        requestId: typeof raw.request_id === 'string' ? raw.request_id : undefined,
        steeringStatus: raw.source === 'steer' ? 'applied' : undefined,
        turn: typeof raw.turn === 'number' ? raw.turn : state.turn })
      return patch
    }

    case 'request/start': {
      patch.generation = readGeneration({ ...(raw.generation as object ?? {}), request_id: raw.request_id })
      return patch
    }
    case 'request/progress': {
      if (state.generation && raw.request_id === state.generation.request_id && raw.generation && typeof raw.generation === 'object') {
        patch.generation = updateGeneration(state.generation, raw.generation as Record<string, unknown>)
      }
      return patch
    }
    case 'request/end': {
      if (!state.generation || raw.request_id === state.generation.request_id) patch.generation = null
      return patch
    }
    case 'assistant/delta': {
      if (state.generation && typeof raw.request_id === 'string' && raw.request_id !== state.generation.request_id) return patch
      const delta = String(raw.delta ?? '')
      if (!delta) return patch
      const deltaType = String(raw.delta_type ?? 'text')
      const draft = { ...state.draft, speaker: readAgentSpeaker(raw.speaker) ?? state.draft.speaker }
      draft.id ??= uid('draft')
      draft.at ??= Date.now()
      if (deltaType === 'thinking') draft.thinking += delta
      else draft.text += delta
      patch.draft = draft
      if (state.generation) patch.generation = generationDelta(state.generation, delta, deltaType === 'thinking')
      patch.running = true
      return patch
    }

    case 'assistant/partial': {
      if (raw.status !== 'stopped' && raw.status !== 'failed' && raw.status !== 'interrupted') return patch
      const author = readTeamAuthor(raw.author)
      if (author) {
        const sourceSeq = typeof raw.source_seq === 'number' ? raw.source_seq : undefined
        if (sourceSeq !== undefined && state.blocks.some(block => block.kind === 'assistant' && block.sourceSeq === sourceSeq)) return patch
        if (typeof raw.text === 'string' && raw.text.trim()) push({id: sourceSeq === undefined ? uid('member-partial') : 'member-partial:' + sourceSeq, kind: 'assistant',
          at: typeof raw.source_time === 'number' ? raw.source_time : Date.now(), sourceSeq,
          turn: typeof raw.turn === 'number' ? raw.turn : state.turn, author, text: raw.text, partial: raw.status,
          checkpointGap: raw.checkpoint_gap === true, streaming: false})
        return patch
      }
      patch.generation = null
      const draft = { ...state.draft, speaker: readAgentSpeaker(raw.speaker) ?? state.draft.speaker,
        text: typeof raw.text === 'string' ? raw.text : state.draft.text,
        thinking: typeof raw.thinking === 'string' ? raw.thinking : state.draft.thinking,
        turn: typeof raw.turn === 'number' ? raw.turn : state.draft.turn }
      finalizeDraft({ ...state, draft }, patch, raw.status, typeof raw.source_seq === 'number' ? raw.source_seq : undefined)
      return patch
    }

    case 'assistant/message': {
      patch.generation = null
      const text = typeof raw.text === 'string' ? raw.text : state.draft.text
      // 完整消息替换流式草稿；不能先归档草稿再追加同一份最终文本。
      finalizeDraft({ ...state, draft: { ...state.draft, text: '', thinking: typeof raw.thinking === 'string' ? raw.thinking : state.draft.thinking } }, patch)
      const usage = (raw.usage ?? null) as TokenUsage | null
      if (usage !== null) {
        const summed = sumUsage(state.usage, usage)
        if (summed === null) patch.error = {key:'ui.http.invalidResponse'}
        else patch.usage = summed
      }
      if (text.trim()) {
        const blocks = (patch.blocks ?? state.blocks).concat([
          {
            id: state.draft.id ? state.draft.id + '-text' : uid('a'),
            kind: 'assistant',
            author: readTeamAuthor(raw.author),
            speaker: readAgentSpeaker(raw.speaker) ?? state.draft.speaker,
            error: raw.error === true,
            sourceSeq: typeof raw.source_seq === 'number' ? raw.source_seq : undefined,
            at: state.draft.at ?? Date.now(),
            text,
            streaming: false,
            usage,
            turn: state.turn,
          },
        ])
        const author = readTeamAuthor(raw.author)
        patch.blocks = author?.role === 'lead' ? blocks.map(block => block.kind === 'team' && block.turn === state.turn && block.team === author.team && block.publicReply?.member === author.member && block.publicReply.stage === author.stage ? { ...block, publicReply: undefined } : block) : blocks
      }
      return patch
    }

    case 'tool/start': {
      const callId = String(raw.call_id ?? uid('c'))
      const turn = typeof raw.turn === 'number' ? raw.turn : state.turn
      const owner = findToolOwner(state, callId, turn)
      // 同一回合的调用身份不可复用，重放开始帧不能重开已完成的调用。
      if (turn < state.turn || owner) return patch
      const block: Block = {
        id: uid('t'),
        kind: 'tool',
        at: Date.now(),
        callId,
        name: String(raw.name ?? 'tool'),
        status: 'running',
        input: raw.input,
        startedAt: Date.now(),
        turn,
      }
      push(block)
      patch.toolCalls = state.toolCalls + 1
      patch.running = true
      return patch
    }

    case 'hook/state': {
      const row = readHookActivity(raw)
      if (!row) return patch
      if (row.event === 'Stop') {
        if (row.turn !== state.turn || !state.running) return patch
        const previous = state.blocks.find(block => block.kind === 'hook' && block.hooks.some(old => old.id === row.id))
        if (!previous) finalizeDraft(state, patch)
        const blocks = patch.blocks ?? state.blocks
        patch.blocks = previous ? blocks.map(block => block.id === previous.id && block.kind === 'hook'
          ? { ...block, hooks: mergeHookActivity(block.hooks, row), sourceSeq: Math.max(block.sourceSeq ?? 0, row.source_seq) } : block)
          : [...blocks, { kind: 'hook', id: `hook:${row.id}`, at: Date.now(), turn: row.turn, sourceSeq: row.source_seq, hooks: [row] }]
        return patch
      }
      // A hook cannot invent a tool invocation or attach to a reused ID in another turn.
      const owner = state.blocks.slice().reverse().find((block) => block.kind === 'tool' && block.callId === row.call_id && block.turn === row.turn)
      if (!owner || owner.kind !== 'tool') return patch
      patch.blocks = state.blocks.map((block) => block.id === owner.id && block.kind === 'tool'
        ? { ...block, hooks: mergeHookActivity(block.hooks, row) } : block)
      return patch
    }

    case 'tool/output': {
      const callId = String(raw.call_id ?? '')
      if (!callId) return patch
      const turn = typeof raw.turn === 'number' ? raw.turn : state.turn
      const owner = state.blocks.slice().reverse().find((block) => block.kind === 'tool' && block.callId === callId && block.turn === turn)
      if (owner) {
        patch.blocks = state.blocks.map((block) => block.id === owner.id && block.kind === 'tool' && block.status === 'running'
          ? { ...block, progress: mergeToolOutput(block.progress, raw) } : block)
      } else if (turn === state.turn) {
        const progress = mergeToolOutput(undefined, raw)
        if (progress) {
          const block: ToolBlockState = { id: uid('t'), kind: 'tool', at: Date.now(), callId, turn, name: String(raw.name ?? 'tool'), status: 'running', progress }
          push(block)
        }
      }
      return patch
    }

    case 'tool/end': {
      const callId = String(raw.call_id ?? '')
      const isError = Boolean(raw.is_error)
      const turn = typeof raw.turn === 'number' ? raw.turn : undefined
      const owner = findToolOwner(state, callId, turn)
      const interrupted = owner?.outcomePending === true
      if (owner && owner.status !== 'running' && !interrupted) return patch
      const target = owner?.id
      if (target) {
        patch.blocks = state.blocks.map((block) =>
          block.id === target && block.kind === 'tool'
            ? {
                ...block,
                status: isError ? 'error' : 'ok',
                outcomePending: false,
                outputKey: undefined,
                turn: typeof raw.turn === 'number' ? raw.turn : block.turn,
                output: raw.content ?? block.output,
                contentRef: raw.content_ref as ToolBlockState['contentRef'],
                display: raw.display,
                isError,
                durationMs:
                  typeof raw.duration_ms === 'number'
                    ? raw.duration_ms
                    : block.startedAt
                      ? Date.now() - block.startedAt
                      : undefined,
              }
            : block,
        )
      } else {
        // 服务端重放了孤立的 tool/end（补发缓冲可能不含配对的 start）。
        push({
          id: uid('t'),
          kind: 'tool',
          at: Date.now(),
          callId: callId || uid('c'),
          name: String(raw.name ?? 'tool'),
          status: isError ? 'error' : 'ok',
          output: raw.content,
          contentRef: raw.content_ref as ToolBlockState['contentRef'],
          display: raw.display,
          isError,
          turn: typeof raw.turn === 'number' ? raw.turn : undefined,
          durationMs: typeof raw.duration_ms === 'number' ? raw.duration_ms : undefined,
        })
      }
      if (state.toolStatsKnown && (turn == null || turn >= state.turn || owner)) {
        const unstarted = raw.display && typeof raw.display === 'object' && 'not_executed' in raw.display && raw.display.not_executed === true
        if (!owner && !unstarted) patch.toolCalls = state.toolCalls + 1
        if (isError && (!owner ? !unstarted : owner.status === 'running' || interrupted || !owner.isError)) patch.toolErrors = state.toolErrors + 1
      } else if (!state.toolStatsKnown) {
        const tools = (patch.blocks ?? state.blocks).filter(block => block.kind === 'tool')
        patch.toolCalls = tools.length
        patch.toolErrors = tools.filter(block => block.status === 'error').length
      }
      const completed = target ? (patch.blocks ?? state.blocks).find(block => block.id === target)
        : (patch.blocks ?? state.blocks).slice().reverse().find((block) => block.kind === 'tool' && block.callId === callId)
      if (completed?.kind === 'tool') {
        const artifacts = presentedArtifacts(completed.name, isError, completed.display, completed.id, completed.at)
        if (artifacts && !(patch.blocks ?? state.blocks).some((block) => block.id === artifacts.id)) {
          patch.blocks = (patch.blocks ?? state.blocks).concat(artifacts)
        }
      }
      return patch
    }

    case 'todo/write': {
      const todos = Array.isArray(raw.todos) ? (raw.todos as TodoItem[]) : []
      patch.todos = todos
      patch.planRevision = state.planRevision + 1
      return patch
    }

    case 'deliverables/presented': {
      // The completed tool result owns the card in both live and saved history.
      // A preliminary broadcast cannot prove that the result was committed.
      return patch
    }

    case 'agent/loaded': {
      patch.agentMount = mergeAgentMount(state.agentMount, raw.mount)
      return patch
    }
    case 'skill/loaded': {
      push({
        id: uid('sk'),
        kind: 'skill',
        turn: state.turn,
        at: Date.now(),
        name: typeof raw.name === 'string' ? raw.name : undefined,
      })
      return patch
    }

    case 'agent/member-stream': {
      patch.blocks = state.blocks.map(block => block.kind === 'team' && block.runId === raw.run_id ? readMemberStream(raw, block) : block)
      return patch
    }

    case 'agent/team': {
      const runId = typeof raw.run_id === 'string' ? raw.run_id : ''
      if (!runId) return patch
      const existing = state.teamIndex[runId] ?? state.blocks.find(block => block.kind === 'team' && block.runId === runId)?.id
      const previous = state.blocks.find(block => block.id === existing)
      const next = readTeamProgress(raw, previous?.kind === 'team' ? previous : undefined)
      if (!next) return patch
      if (next.turn === undefined) next.turn = state.turn
      if (existing) patch.blocks = state.blocks.map(block => block.id === existing ? next : block)
      else push(next)
      patch.teamIndex = { ...state.teamIndex, [runId]: next.id }
      return patch
    }

    case 'compaction/start': {
      push({ id: uid('cp'), kind: 'compaction', at: Date.now(), phase: 'start', turn: state.turn,
        compactionId: typeof raw.compaction_id === 'number' ? raw.compaction_id : undefined })
      return patch
    }

    case 'compaction/progress': {
      const completed = raw.completed_chunks, total = raw.total_chunks, id = raw.compaction_id
      if (typeof completed !== 'number' || typeof total !== 'number' || !Number.isSafeInteger(completed)
        || !Number.isSafeInteger(total) || completed < 0 || total < 1 || completed > total || typeof id !== 'number') return patch
      const blocks = state.blocks.slice()
      const index = blocks.findIndex(block => block.kind === 'compaction' && block.phase === 'start' && block.compactionId === id)
      const block = blocks[index]
      if (!block || block.kind !== 'compaction') return patch
      const stage = raw.stage === 'merge' ? 'merge' : 'chunks'
      const level = typeof raw.merge_level === 'number' && Number.isSafeInteger(raw.merge_level) ? raw.merge_level : 0
      if ((block.stage === 'merge' && stage !== 'merge') || level < (block.mergeLevel ?? 0)) return patch
      const sameStage = (block.stage ?? 'chunks') === stage && (block.mergeLevel ?? 0) === level
      if (sameStage && (completed < (block.completedChunks ?? 0)
        || (block.totalChunks !== undefined && block.totalChunks !== total))) return patch
      const validNumber = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined
      blocks[index] = { ...block, completedChunks: completed, totalChunks: total, stage, mergeLevel: level,
        currentChunk: validNumber(raw.current_chunk), attempt: validNumber(raw.attempt),
        maxAttempts: validNumber(raw.max_attempts), retryInSeconds: validNumber(raw.retry_in_seconds) }
      patch.blocks = blocks
      return patch
    }

    case 'compaction/prune':
    case 'compaction/end': {
      const blocks = state.blocks.slice()
      for (let i = blocks.length - 1; i >= 0; i -= 1) {
        const block = blocks[i]!
        if (block.kind === 'compaction' && block.phase === 'start'
          && (raw.compaction_id === undefined || block.compactionId === raw.compaction_id)) {
          blocks[i] = {
            ...block,
            phase: 'end',
            pruned: Number(raw.pruned ?? block.pruned ?? 0),
            charsRemoved: Number(raw.chars_removed ?? block.charsRemoved ?? 0),
            error: typeof raw.error === 'string' ? raw.error : undefined,
            skipped: raw.skipped === true,
            cancelled: raw.cancelled === true,
          }
          patch.blocks = blocks
          return patch
        }
      }
      if (raw.compaction_id !== undefined) return patch
      push({
        id: uid('cp'),
        kind: 'compaction',
        at: Date.now(),
        phase: 'end',
        pruned: Number(raw.pruned ?? 0),
        charsRemoved: Number(raw.chars_removed ?? 0),
        error: typeof raw.error === 'string' ? raw.error : undefined,
        skipped: raw.skipped === true,
        cancelled: raw.cancelled === true,
      })
      return patch
    }

    case 'subagent/status':
    case 'subagent/message': {
      const agentId = String(raw.agent_id ?? '').trim()
      if (!agentId) return patch
      const existing = state.agentIndex[agentId]
      const previous = existing ? state.blocks.find(block => block.id === existing) : undefined
      const current = previous?.kind === 'subagent' ? previous : undefined
      const status = String(raw.status ?? current?.status ?? 'running')
      const description = typeof raw.description === 'string' && raw.description.trim() ? raw.description : current?.description ?? ''
      const next: Block = {
        id: existing ?? uid('sa'),
        kind: 'subagent',
        turn: current?.turn ?? state.turn,
        at: Date.now(),
        agentId,
        description,
        status,
        result: typeof raw.result === 'string' ? raw.result : current?.result,
        error: typeof raw.error === 'string' || raw.error === null ? raw.error : current?.error ?? null,
        durationMs: typeof raw.duration_ms === 'number' || raw.duration_ms === null ? raw.duration_ms : current?.durationMs ?? null,
        toolCalls: typeof raw.tool_calls === 'number' ? raw.tool_calls : current?.toolCalls,
      }
      if (existing) {
        patch.blocks = state.blocks.map((block) => (block.id === existing ? next : block))
      } else {
        push(next)
        patch.subagents = state.subagents + 1
      }
      patch.agentIndex = { ...state.agentIndex, [agentId]: next.id }
      return patch
    }

    case 'error': {
      patch.generation = null
      const messageCopy = runErrorCopy(raw)
      const messageKey = raw.message == null && raw.error == null ? "ui.store.chat.13a46616e16f" : undefined
      const message = String(raw.message ?? raw.error ?? '')
      const last = state.blocks[state.blocks.length - 1]
      const code = typeof raw.code === 'string' ? raw.code : undefined
      const failure: import('@/types').ErrorBlock = {
        id: uid('e'), kind: 'error', at: Date.now(), message, messageCopy, messageKey, code,
        details: raw.details, turn: state.turn,
      }
      if (last?.kind === 'error' && !state.running && sameTurnFailure(last, failure)) {
        patch.blocks = state.blocks.map(block => block === last ? {...block,
          messageCopy: messageCopy ?? last.messageCopy, details: raw.details ?? last.details} : block)
        patch.error = messageCopy ?? last.messageCopy ?? (messageKey ? { key: messageKey } : message)
        return patch
      }
      finalizeDraft(state, patch)
      patch.blocks = (patch.blocks ?? state.blocks).concat(failure)
      patch.error = messageCopy ?? (messageKey ? { key: messageKey } : message)
      patch.running = false
      return patch
    }

    case 'turn/end': {
      // A late end cannot finalize a newer turn recovered from a snapshot.
      if (typeof raw.turn === 'number' && raw.turn < state.turn) return patch
      if (raw.committed !== false) patch.turnActivity = mergeTurnActivity(state.turnActivity, [{ type: event.type, data: raw, time: typeof raw.time === 'number' ? raw.time : Date.now(), seq: typeof raw.source_seq === 'number' ? raw.source_seq : undefined }])
      patch.queueRunning = false
      patch.generation = null
      patch.running = false
      patch.stopping = false
      finalizeDraft(state, patch)
      patch.blocks = settleOpenTools(patch.blocks ?? state.blocks)
      const outcome = turnOutcome(raw, uid('turn-outcome'), Date.now())
      if (outcome) {
        outcome.turn ??= state.turn
        outcome.sourceSeq = typeof raw.source_seq === 'number' ? raw.source_seq : undefined
        outcome.historical = false
        const last = patch.blocks[patch.blocks.length - 1]
        if (outcome.kind === 'error') {
          const sameFailure = last?.kind === 'error' && sameTurnFailure(last, outcome)
          if (!sameFailure) patch.blocks = patch.blocks.concat(outcome)
          else patch.blocks = patch.blocks.map(block => block === last ? {...block,
            messageCopy: outcome.messageCopy ?? last.messageCopy, details: outcome.details ?? last.details,
            sourceSeq: outcome.sourceSeq ?? last.sourceSeq, historical: false} : block)
          patch.error = outcome.messageCopy ?? (outcome.messageKey ? { key: outcome.messageKey } : outcome.message)
        } else if (!patch.blocks.some(block => block.kind === 'turn' && block.phase === 'end' && block.turn === outcome.turn)) {
          patch.blocks = patch.blocks.concat(outcome)
        }
      }
      return patch
    }

    case 'done': {
      patch.queueRunning = false
      patch.generation = null
      patch.running = false
      patch.stopping = false
      finalizeDraft(state, patch)
      patch.blocks = settleOpenTools(patch.blocks ?? state.blocks)
      return patch
    }

    case 'run/state': {
      if (raw.kind === 'context_compact') return patch
      if (raw.started_at === null) return patch
      if (['completed', 'failed', 'interrupted'].includes(String(raw.status))) {
        patch.generation = null
        patch.running = false
        patch.stopping = false
        finalizeDraft(state, patch)
        patch.blocks = settleOpenTools(patch.blocks ?? state.blocks)
      }
      return patch
    }

    case 'queue_state': {
      // HTTP control receipts omit counts: only the socket owns live activity.
      if (typeof raw.updated_at === 'number' && state.queueControlAt !== null && raw.updated_at < state.queueControlAt) return patch
      if (typeof raw.updated_at === 'number') patch.queueControlAt = raw.updated_at
      patch.queued = typeof raw.pending === 'number' ? raw.pending : state.queued
      patch.queueRunning = typeof raw.running === 'boolean' ? raw.running : state.queueRunning
      patch.queuePaused = typeof raw.paused === 'boolean' ? raw.paused : state.queuePaused
      if (typeof raw.pause_reason === 'string') patch.queuePauseReason = raw.pause_reason
      if (typeof raw.waiting_reason === 'string') patch.queueWaitingReason = raw.waiting_reason === 'environment' ? 'environment' : ''
      if (typeof raw.running === 'boolean' || typeof raw.paused === 'boolean') {
        patch.running = patch.queueRunning
        if (!patch.running) patch.stopping = false
      }
      return patch
    }

    case 'goal/message': {
      if (typeof raw.source_seq === 'number' && typeof raw.goal_id === 'string') {
        patch.blocks = state.blocks.map(block => block.kind === 'user' && block.sourceSeq === raw.source_seq ? {...block, goalId: raw.goal_id as string} : block)
      }
      return patch
    }
    case 'goal/state': {
      const next = raw as unknown as import('@/types').GoalState
      if ((next.stateVersion ?? 0) < (state.goalState.stateVersion ?? 0)) return patch
      if (next.goal && state.goalState.goal?.id === next.goal.id && next.goal.revision < state.goalState.goal.revision) return patch
      patch.goalState = { goal: next.goal, activation: next.activation, stateVersion: next.stateVersion }
      return patch
    }
    case 'plan_mode': {
      const mode = readPlanModeState(raw)
      if (!mode) return patch
      patch.planMode = effectivePlanMode(mode)
      patch.planModeState = mode
      patch.planModeRevision = state.planModeRevision + 1
      return patch
    }
    case 'plan/review': {
      patch.planRevision = state.planRevision + 1
      return patch
    }

    case 'stopping': {
      patch.stopping = Boolean(raw.cancelled ?? true)
      return patch
    }
    case 'user_question/ask': {
      const questionId = String(raw.question_id ?? '')
      if (!questionId || !Array.isArray(raw.questions)) return patch
      if (!state.blocks.some((block) => block.kind === 'question' && block.questionId === questionId)) {
        push({ id: 'question-' + questionId, kind: 'question', at: Date.now(), questionId, questions: raw.questions as PendingQuestion['questions'] })
      }
      return patch
    }
    case 'user_question/closed': {
      const questionId = String(raw.question_id ?? '')
      patch.questions = state.questions.filter((q) => q.question_id !== questionId)
      patch.blocks = state.blocks.map((block) => block.kind === 'question' && block.questionId === questionId && !block.answered ? { ...block, answered: true, closed: true } : block)
      return patch
    }
    case 'user_question/answered':
    case 'question_answered': {
      const questionId = String(raw.question_id ?? '')
      patch.questions = state.questions.filter((q) => q.question_id !== questionId)
      patch.blocks = state.blocks.map((block) => block.kind === 'question' && block.questionId === questionId ? { ...block, answered: true, closed: false, answers: raw.answers ?? block.answers } : block)
      return patch
    }

    case 'ready': {
      const previews = Array.isArray(raw.member_previews) ? raw.member_previews.slice(0,32).filter((row): row is Record<string, unknown> => Boolean(row && typeof row === 'object' && !Array.isArray(row))) : []
      patch.blocks = state.blocks.map(block => block.kind === 'team' ? previews.reduce<TeamBlock>((next, frame) => readMemberStream(frame, next), {...block, previews: undefined}) : block)
      patch.generation = raw.running === false ? null : readGeneration(raw.generation)
      // ready.replay 里是本进程启动以来的广播缓存；快照已从 REST 拿到权威历史，
      // 直接丢弃，避免整段对话重影。last_seq 才是真正的续传水位。
      patch.serverId = typeof raw.server_id === 'string' ? raw.server_id : state.serverId
      const lastSeq = raw.last_seq
      if (typeof lastSeq === 'number' && Number.isSafeInteger(lastSeq) && lastSeq >= 0) {
        patch.lastSeq = Math.max(state.lastSeq, lastSeq)
      }
      patch.queuePaused = Boolean(raw.paused)
      patch.queuePauseReason = typeof raw.pause_reason === 'string' ? raw.pause_reason : ''
      patch.queueWaitingReason = raw.waiting_reason === 'environment' ? 'environment' : ''
      patch.queueControlAt = typeof raw.updated_at === 'number' ? raw.updated_at : null
      patch.queueRunning = Boolean(raw.running)
      patch.running = Boolean(raw.running)
      if (!patch.running) patch.stopping = false
      patch.queued = Number(raw.pending ?? 0)
      patch.draft = {text:'', thinking:'', turn:state.turn}
      if (raw.draft && typeof raw.draft === 'object' && !Array.isArray(raw.draft)) {
        const draft = raw.draft as Record<string, unknown>
        patch.draft = { speaker: readAgentSpeaker(draft.speaker), text: String(draft.text ?? ''), thinking: String(draft.thinking ?? ''), turn: typeof draft.turn === 'number' ? draft.turn : undefined }
        if (patch.draft.text || patch.draft.thinking) {
          patch.draft.id = uid('draft')
          patch.draft.at = Date.now()
        }
      }
      return patch
    }

    case 'steer/state': {
      const receipts = mergeSteering(state.steering, raw.receipts ?? raw.receipt)
      patch.steering = receipts.filter((receipt) => receipt.turn === state.turn)
      const blocks = state.blocks.slice()
      for (const receipt of receipts) {
        const index = blocks.findIndex(block => block.kind === 'user' && block.requestId === receipt.request_id)
        if (index >= 0) {
          const old = blocks[index] as UserBlock
          if (old.sourceSeq !== receipt.accepted_seq || old.steeringStatus !== 'pending' && receipt.status === 'pending') continue
          blocks[index] = { ...old, steeringStatus: receipt.status, steeringReason: receipt.reason,
            steeringTeamDelivered: receipt.team_delivered, steeringDeliverySeq: receipt.delivery_seq,
            text: receipt.text_truncated ? old.text : receipt.text,
            attachments: receipt.attachments ?? old.attachments }
        } else if (!receipt.text_truncated) {
          const block: UserBlock = { id: 'steer:' + receipt.request_id, kind: 'user',
            at: receipt.accepted_at, sourceSeq: receipt.accepted_seq, turn: receipt.turn,
            text: receipt.text, attachments: receipt.attachments, requestId: receipt.request_id, steeringStatus: receipt.status, steeringReason: receipt.reason,
            steeringTeamDelivered: receipt.team_delivered, steeringDeliverySeq: receipt.delivery_seq }
          const after = blocks.findIndex(item => item.sourceSeq !== undefined && item.sourceSeq > receipt.accepted_seq)
          blocks.splice(after < 0 ? blocks.length : after, 0, block)
        }
      }
      patch.blocks = blocks
      return patch
    }

    case 'resumed': {
      patch.lastSeq = Math.max(state.lastSeq, Number(raw.last_seq ?? 0))
      if (typeof raw.paused === 'boolean') {
        Object.assign(patch, reduceEvent(state, { ...event, type: 'queue_state', seq: undefined }))
      }
      return patch
    }

    case 'pong':
    case 'notice/repeat-tool':
      return patch

    default:
      return patch
  }
}

function findToolOwner(state: ChatSessionState, callId: string, turn?: number): import('@/types').ToolBlockState | undefined {
  return state.blocks.slice().reverse().find((block): block is import('@/types').ToolBlockState => block.kind === 'tool' && block.callId === callId &&
    (turn == null || block.turn === turn || (block.turn == null || block.turn === 0) && block.status === 'running'))
}

/* ==================================================================
   Store
   ================================================================== */

export interface ChatStore {
  sessions: Record<string, ChatSessionState>
  hydrate: (id: string, detail: SessionDetail) => void
  loadOlder: (id: string) => Promise<void>
  markLoading: (id: string, loading: boolean) => void
  setConnection: (
    id: string,
    connection: ConnectionState,
    detail?: { attempt?: number; nextRetryMs?: number },
  ) => void
  apply: (id: string, event: StreamEvent) => void
  applyMany: (id: string, events: StreamEvent[]) => void
  setPlanMode: (id: string, mode: ConfirmedPlanMode) => void
  setQuestions: (id: string, questions: PendingQuestion[]) => void
  setAgentMount: (id: string, mount: AgentMountState) => void
  appendLocalUser: (id: string, text: string, attachments?: AttachmentInfo[]) => void
  drop: (id: string) => void
  get: (id: string) => ChatSessionState
}

let historyGeneration = 0

const ensure = (
  sessions: Record<string, ChatSessionState>,
  id: string,
): ChatSessionState => Object.hasOwn(sessions, id) ? sessions[id] : emptySession()

export const useChatStore = create<ChatStore>((set, get) => ({
  sessions: {},

  hydrate: (id, detail) =>
    set((state) => {
      const current = ensure(state.sessions, id)
      let blocks = blocksFromDetail(detail)
      let history: LoadedHistory | null = detail.history ? { ...detail.history, cursorThrough: detail.history.through,
        generation: ++historyGeneration, revision: current.history?.revision ?? 0,
        loading: false, error: null, resetRequired: false, ids: blocks.map((block) => block.id) } : null
      // Appends/compaction keep committed sequence order. Preserve an already
      // loaded prefix only when the fresh window overlaps it; never invent a gap.
      if (history && current.history && !current.history.resetRequired && history.through >= current.history.through &&
          history.start <= current.history.end && current.history.start < history.start) {
        const freshIds = new Set(history.ids), oldIds = new Set(current.history.ids)
        const first = Math.min(...blocks.filter((block) => block.sourceSeq != null).map((block) => block.sourceSeq!))
        const prefix = current.blocks.filter((block) => oldIds.has(block.id) && !freshIds.has(block.id) && block.sourceSeq != null && block.sourceSeq < first &&
          !(block.kind === 'tool' && block.status === 'running'))
        blocks = prefix.concat(blocks)
        blocks = linkLoadedOutcomes(blocks)
        history = { ...history, start: current.history.start, has_more: current.history.has_more,
          next_cursor: current.history.next_cursor, cursorThrough: current.history.cursorThrough, ids: blocks.map((block) => block.id) }
      }
      if (detail.storage_error) blocks.push({ id: uid('storage'), kind: 'error', at: Date.now(), message: detail.storage_error, code: 'session/persistence' })
      if (detail.interrupted) blocks.push({ id: uid('interrupted'), kind: 'notice', at: Date.now(), text: { key: "ui.store.chat.701db4b527c4" }, tone: 'warn' })
      const planMode: PlanModeState = detail.plan_mode ?? { active: false }
      return {
        sessions: {
          ...state.sessions,
          [id]: {
            ...current,
            blocks,
            turnActivity: mergeTurnActivity(current.turnActivity, detail.turn_events ?? detail.events ?? []),
            history,
            ...restoredToolStats(detail, blocks),
            agentIndex: {},
            teamIndex: Object.fromEntries(blocks.filter(block => block.kind === "team").map(block => [block.runId, block.id])),
            loading: false,
            hydrated: true,
            turn: detail.turn ?? current.turn,
            steering: mergeSteering([], detail.steering ?? []),
            goalState: detail.goal_state && (detail.goal_state.stateVersion ?? 0) >= (current.goalState.stateVersion ?? 0) ? detail.goal_state : current.goalState,
            todos: detail.todos ?? current.todos,
            questions: detail.questions ?? current.questions,
            prompts: detail.prompts ?? current.prompts,
            agentMount: mergeAgentMount(current.agentMount, detail.agent_mount),
            planMode: effectivePlanMode(readPlanModeState(planMode) ?? current.planModeState),
            planModeState: readPlanModeState(planMode) ?? current.planModeState,
            planModeRevision: current.planModeRevision + 1,
            usage: detail.usage ?? current.usage,
            error: null,
            running: false,
            generation: null,
            draft: { text: '', thinking: '' },
          },
        },
      }
    }),

  loadOlder: async (id) => {
    const initial = get().get(id).history
    if (!initial || initial.loading || !initial.has_more || !initial.next_cursor || initial.resetRequired) return
    const generation = initial.generation, cursor = initial.next_cursor
    const patchHistory = (change: Partial<LoadedHistory>) => set((state) => {
      const current = Object.hasOwn(state.sessions, id) ? state.sessions[id] : undefined
      if (!current?.history || current.history.generation !== generation || current.history.next_cursor !== cursor) return state
      return { sessions: { ...state.sessions, [id]: { ...current, history: { ...current.history, ...change } } } }
    })
    patchHistory({ loading: true, error: null })
    try {
      const detail = await runtimeRequest<SessionDetail>('/sessions/' + encodeURIComponent(id) + '/conversation?cursor=' + encodeURIComponent(cursor))
      set((state) => {
        const current = Object.hasOwn(state.sessions, id) ? state.sessions[id] : undefined, history = current?.history, page = detail.history
        if (!history || history.generation !== generation || history.next_cursor !== cursor) return state
        if (detail.header?.id !== id || !page || page.through !== history.cursorThrough || page.end !== history.start || page.start >= page.end) {
          return { sessions: { ...state.sessions, [id]: { ...current, history: { ...history, loading: false,
            error: { key: "ui.store.chat.d15acd329968" }, resetRequired: true } } } }
        }
        const existing = new Set(current.blocks.map((block) => block.id))
        const older = blocksFromDetail(detail).filter((block) => !existing.has(block.id))
        // Keep current blocks and indexes intact: tool output, draft, questions
        // and optimistic messages may have changed while this request was open.
        return { sessions: { ...state.sessions, [id]: { ...current, blocks: linkLoadedOutcomes(older.concat(current.blocks)),
          turnActivity: mergeTurnActivity(current.turnActivity, detail.turn_events ?? detail.events ?? []),
          ...(!current.toolStatsKnown ? restoredToolStats({ ...detail, stats: {} }, older.concat(current.blocks)) : {}),
          history: { ...history, start: page.start, has_more: page.has_more, next_cursor: page.next_cursor,
            loading: false, error: null, revision: history.revision + 1, ids: older.map((block) => block.id).concat(history.ids) } } } }
      })
    } catch (error) {
      patchHistory({ loading: false, error: error instanceof ApiError && error.failure.copy ? error.failure.copy : error instanceof Error ? error.message : { key: "ui.store.chat.d368dae767d0" },
        resetRequired: error instanceof ApiError && ['conversation/history-changed', 'conversation/invalid-cursor'].includes(error.code) })
    }
  },

  markLoading: (id, loading) =>
    set((state) => ({
      sessions: { ...state.sessions, [id]: { ...ensure(state.sessions, id), loading } },
    })),

  setConnection: (id, connection, detail) =>
    set((state) => ({
      sessions: {
        ...state.sessions,
        [id]: {
          ...ensure(state.sessions, id),
          connection,
          attempt: detail?.attempt ?? 0,
          retryIn: detail?.nextRetryMs ?? 0,
        },
      },
    })),

  apply: (id, event) =>
    set((state) => {
      if (event.type === 'hook/state' && event.session_id !== id) return state
      const current = ensure(state.sessions, id)
      const patch = reduceEvent(current, event)
      if (Object.keys(patch).length === 0) return state
      return { sessions: { ...state.sessions, [id]: { ...current, ...patch } } }
    }),

  applyMany: (id, events) =>
    set((state) => {
      let current = ensure(state.sessions, id)
      for (const event of events) {
        if (event.type === 'hook/state' && event.session_id !== id) continue
        const patch = reduceEvent(current, event)
        if (Object.keys(patch).length === 0) continue
        current = { ...current, ...patch }
      }
      return { sessions: { ...state.sessions, [id]: current } }
    }),

  setPlanMode: (id, mode) =>
    set((state) => ({
      sessions: { ...state.sessions, [id]: { ...ensure(state.sessions, id), planMode: effectivePlanMode(mode), planModeState: mode, planModeRevision: ensure(state.sessions, id).planModeRevision + 1 } },
    })),

  setAgentMount: (id, mount) =>
    set(state => ({ sessions: { ...state.sessions, [id]: {
      ...ensure(state.sessions, id), agentMount: mergeAgentMount(ensure(state.sessions, id).agentMount, mount),
    } } })),

  setQuestions: (id, questions) =>
    set((state) => {
      const current = ensure(state.sessions, id)
      const pending = new Set(questions.map((question) => question.question_id))
      const blocks = current.blocks.map((block) => block.kind === 'question' && !block.answered && !pending.has(block.questionId) ? { ...block, answered: true, closed: true } : block)
      const existing = new Set(blocks.filter((block) => block.kind === 'question').map((block) => block.questionId))
      for (const question of questions) {
        if (!existing.has(question.question_id)) blocks.push({ id: 'question-' + question.question_id, kind: 'question', at: Date.now(), questionId: question.question_id, questions: question.questions })
      }
      return { sessions: { ...state.sessions, [id]: { ...current, questions, blocks } } }
    }),

  /** 乐观上屏：不等服务端回显，回显到达时由 reducer 排重。 */
  appendLocalUser: (id, text, attachments) =>
    set((state) => {
      const current = ensure(state.sessions, id)
      const blocks = current.blocks.concat([
        {
          id: uid('u'),
          kind: 'user',
          at: Date.now(),
          text,
          // 空数组与 undefined 等价，但留着 undefined 能让历史消息不带这个键，
          // 序列化大小更小、比较更廉价。
          attachments: attachments && attachments.length > 0 ? attachments : undefined,
          turn: current.running ? current.turn : undefined,
        } satisfies Block,
      ])
      return { sessions: { ...state.sessions, [id]: { ...current, blocks } } }
    }),

  drop: (id) =>
    set((state) => {
      const next = { ...state.sessions }
      delete next[id]
      return { sessions: next }
    }),

  get: (id) => ensure(get().sessions, id),
}))

/** 便捷选择器。 */
export function useChat(id: string | null): ChatSessionState {
  return useChatStore((state) => (id && Object.hasOwn(state.sessions, id) ? state.sessions[id] : EMPTY))
}

const EMPTY = emptySession()
