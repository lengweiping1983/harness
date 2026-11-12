/**
 * 前后端契约类型。
 *
 * 全部对应 `harness/web/` 下 Python 侧实际吐出的 JSON 形状，
 * 所有字段按"服务端可能不吐"来写（可选 + 运行时兜底），
 * 这样后端加字段不会让前端崩。
 */
import type { CommandOutput, ConversationHistory, HookActivity as ProtocolHookActivity, ToolResultReference, ModelCredentialStatus } from '@/lib/protocol.generated'
export type HookActivity = ProtocolHookActivity & { reasonCopy?: ResourceCopy }
export type { ConversationHistory } from '@/lib/protocol.generated'

/* ============================ 会话 ============================ */

export interface SessionHeader {
  id: string
  title?: string | null
  cwd?: string | null
  /** Server-resolved filesystem identity; recorded cwd remains unchanged. */
  resolved_cwd?: string | null
  created_at?: number
  updated_at?: number
  metadata?: { archived?: boolean; [key: string]: unknown }
  [key: string]: unknown
}

export interface SessionSummary extends SessionHeader {
  summary?: string | null
  message_count?: number
  last_activity?: number
  /** 会话级模型；缺省表示跟随全局默认。 */
  model?: string | null
  /** 会话级思考强度；缺省表示跟随全局默认。 */
  reasoning_effort?: string | null
}

/** 一个注册过的项目（目录）。会话按 cwd 归入项目。 */
export interface WorkspaceInfo {
  /** 归一化后的绝对路径，兼作主键。 */
  id: string
  path: string
  name: string
  color: string
  created_at?: number
  last_used_at?: number
  /** 目录是否还在（磁盘上被删 / 改名）。 */
  exists?: boolean
}

/** 模型目录条目。 */
export interface ModelInfo {
  id: string
  label: string
  provider: string
  description?: string
  context_window?: number
  max_output?: number
  supports_reasoning?: boolean
  reasoning_efforts?: string[]
  /** 是否是当前全局默认模型。 */
  active?: boolean
  /** 设置面板里是否勾选（勾了才会出现在选择器里）。 */
  visible?: boolean
}

export interface ModelCatalog {
  models: ModelInfo[]
  active: string
  providers: string[]
  reasoning_effort?: string | null
}

/** 目录浏览（项目选择器用）：只列一层子目录。 */
export interface DirEntry {
  name: string
  path: string
}

export interface DirectoryListing {
  path: string
  name: string
  /** 已在文件系统根时为 null。 */
  parent: string | null
  entries: DirEntry[]
  truncated?: boolean
  /** Windows 上已挂载的盘符；其它系统为空。 */
  roots?: DirEntry[]
}

/** 运行时设置（后端持久化）。 */
export interface UiSettings {
  language?: 'auto' | 'zh-CN' | 'en'
  effective_language?: 'zh-CN' | 'en'
  system_region?: string | null
  detection_source?: string
  default_model?: string
  reasoning_effort?: string | null
  /** 模型 id -> 是否在选择器中可见。 */
  model_visibility?: Record<string, boolean>
  [key: string]: unknown
}

/** 回合结束时 present 工具声明的交付物。 */
export interface DeliverableFile {
  path: string
  name: string
  description?: string
  size_bytes?: number
}

export type MessageRole = 'user' | 'assistant' | 'tool' | 'system' | 'developer' | 'unknown'

export interface ToolUseInfo {
  id?: string
  call_id?: string
  name?: string
  input?: unknown
  [key: string]: unknown
}

export interface TokenUsage {
  input_tokens?: number
  output_tokens?: number
  cache_read_tokens?: number
  cache_reported_input_tokens?: number
  reasoning_tokens?: number
  total_tokens?: number
  requests?: number
  [key: string]: unknown
}

export interface ToolResultInfo {
  content_ref?: ToolResultReference
  hooks?: HookActivity[]
  input?: unknown
  progress?: ToolOutputPreview
  approval_decision?: string[]
  approval_reason?: string
  approval_reviewer?: string
  call_source_seq?: number
  tool_use_id?: string
  name?: string
  content?: unknown
  display?: unknown
  is_error?: boolean
  duration_ms?: number
  [key: string]: unknown
}

/** 系统/开发者提示词：只读出口，不进对话时间线。 */
export interface SessionPrompt {
  role: 'system' | 'developer'
  text: string
}

export interface SessionMessage {
  attachments?: AttachmentInfo[]
  goal_id?: string
  partial?: 'stopped' | 'failed' | 'interrupted'
  source_seq?: number
  source_time?: number
  source?: string
  kind: string
  role: MessageRole
  text?: string
  thinking?: string | null
  tool_uses?: ToolUseInfo[]
  results?: ToolResultInfo[]
  usage?: TokenUsage | null
  model?: string | null
  stop_reason?: string | null
  [key: string]: unknown
}

export interface SessionEvent {
  type: string
  seq?: number
  time?: number
  data?: Record<string, unknown>
  [key: string]: unknown
}

export interface TodoItem {
  id?: string
  content?: string
  status?: 'pending' | 'in_progress' | 'completed' | string
  [key: string]: unknown
}

export interface ToolTimelineItem {
  turn?: number
  status?: 'running' | 'done' | 'error' | 'interrupted'
  counted?: boolean
  seq?: number
  time?: number
  name?: string | null
  call_id?: string | null
  is_error?: boolean
  duration_ms?: number | null
  input?: string
  display?: string
}

export interface SessionStats {
  events?: number
  duration_ms?: number
  tools?: { name: string; count: number; errors: number }[]
  tool_calls?: number
  tool_errors?: number
  compactions?: number
  started_at?: number | null
  updated_at?: number | null
}

export interface PlanModeState {
  active: boolean
  [key: string]: unknown
}

export interface QuestionOption {
  id?: string
  label?: string
  description?: string
  [key: string]: unknown
}

export interface QuestionItem {
  id: string
  question: string
  header?: string
  options?: QuestionOption[]
  multi_select?: boolean
  operation?: import('@/lib/protocol.generated').PermissionOperation
  [key: string]: unknown
}

export interface PendingQuestion {
  question_id: string
  session_id: string
  call_id?: string
  created_at?: number
  questions: QuestionItem[]
}

export interface GoalEvidenceRef {
  sourceSeq: number
  callId: string
  name: string
  input: string
  preview: string
  truncated: boolean
}
export interface GoalEvidencePage extends GoalEvidenceRef {
  text: string
  offset: number
  totalChars: number
  nextOffset: number | null
}

export interface GoalState {
  stateVersion?: number
  activation: 'armed' | 'disarmed'
  goal: {
    id: string
    revision: number
    objective: string
    phase: 'active' | 'paused' | 'blocked' | 'complete'
    /** Derived machine status; phase remains the persisted lifecycle field. */
    status?: 'active' | 'paused' | 'blocked' | 'usage_limited' | 'budget_limited' | 'complete'
    elapsedSeconds?: number
    timingActive?: boolean
    roundsStarted: number
    maxGoalRounds: number | null
    blockedReason?: { code: string; message: string }
    stopReason?: string | null
    criteria?: string[]
    completion?: { criterion: number; summary: string; evidence: GoalEvidenceRef[] }[]
    tokenBudget?: number | null
    tokensUsed?: number
    tokensReserved?: number
    tokensUncertain?: number
    retryAt?: number | null
    retryCount?: number
    retryError?: string | null
  } | null
}

export interface SessionDetail {
  agent_mount?: import('@/lib/agentMount').AgentMountState
  turn_events?: SessionEvent[]
  goal_state?: GoalState
  permissions?: import('@/lib/protocol.generated').SessionPermissionState
  turn?: number
  steering?: import('@/lib/protocol.generated').SteeringReceipt[]
  storage_error?: string | null
  interrupted?: boolean
  header: SessionHeader
  messages: SessionMessage[]
  events: SessionEvent[]
  /** Only UI restoration events when negotiated; stats.events still counts the full log. */
  events_view?: 'conversation' | 'conversation-window'
  history?: ConversationHistory
  usage: TokenUsage
  todos: TodoItem[]
  stats: SessionStats
  timeline: ToolTimelineItem[]
  plan_mode?: PlanModeState
  questions?: PendingQuestion[]
  prompts?: SessionPrompt[]
}

/* ============================ 目录 / 状态 ============================ */

export interface ToolSchema {
  name: string
  description?: string
  parameters?: JSONSchema
  /** 分类 id（file / shell / web ...），由后端在投影目录时补上。 */
  category?: string
  category_label?: string
  title?: string
  dangerous?: boolean
  [key: string]: unknown
}

export interface JSONSchema {
  type?: string
  description?: string
  properties?: Record<string, JSONSchema>
  items?: JSONSchema
  enum?: unknown[]
  required?: string[]
  default?: unknown
  [key: string]: unknown
}

export interface SkillInfo {
  name: string
  description?: string
  path?: string
  [key: string]: unknown
}

export interface HarnessConfig {
  model?: string
  provider?: string
  cwd?: string
  enable_plan_mode?: boolean
  [key: string]: unknown
}

export interface StatusPayload extends Partial<ModelCredentialStatus> {
  agent?: Record<string, unknown> | null
  config?: HarnessConfig
  tools?: string[]
  skills?: string[]
  context_warning_ratio?: number
  mcp_connected?: string[]
  live?: { sessions?: number; observers?: number; loop_running?: boolean }
  [key: string]: unknown
}

export interface WorkspaceEntry {
  name: string
  path: string
  type: 'file' | 'directory'
  size?: number
  modified?: number
}

export interface WorkspacePayload {
  root: string
  /** 当前列出的目录相对工作区根的路径；根目录为 ""。 */
  path: string
  /** 面包屑层级（不含根），逐级可点。 */
  parents: string[]
  /** 上一级相对路径；已在根目录时为 ""。 */
  parent: string
  exists: boolean
  /** 当前路径本身是否是目录（false 说明路径不存在或是文件）。 */
  is_dir: boolean
  /** 当前过滤结果是否还有下一页。 */
  truncated: boolean
  next_offset?: number | null
  query?: string
  entries: WorkspaceEntry[]
}

/* ============================ 实时事件 ============================ */

export interface ReadyEvent { type: 'ready'; session_id: string; [k: string]: unknown }
export interface UserMessageEvent { type: 'user/message'; text: string; [k: string]: unknown }
export interface AssistantDeltaEvent {
  type: 'assistant/delta'
  delta_type?: 'text' | 'thinking' | string
  delta?: string
  [k: string]: unknown
}
export interface AssistantMessageEvent {
  type: 'assistant/message'
  text?: string
  thinking?: string
  tool_calls?: ToolUseInfo[]
  usage?: TokenUsage | null
  turn?: number
  step?: number
  [k: string]: unknown
}
export interface ToolStartEvent {
  type: 'tool/start'
  call_id: string
  name: string
  input?: unknown
  [k: string]: unknown
}
export interface ToolEndEvent {
  type: 'tool/end'
  call_id: string
  name: string
  is_error?: boolean
  content?: unknown
  display?: unknown
  duration_ms?: number
  [k: string]: unknown
}
export interface ToolOutputEvent extends CommandOutput {
  [k: string]: unknown
}
export interface TodoWriteEvent { type: 'todo/write'; todos?: TodoItem[]; [k: string]: unknown }
export interface SkillLoadedEvent { type: 'skill/loaded'; [k: string]: unknown }
export interface SubagentStatusEvent {
  type: 'subagent/status'
  agent_id?: string
  description?: string
  status?: string
  duration_ms?: number | null
  tool_calls?: number
  [k: string]: unknown
}
export interface SubagentMessageEvent {
  type: 'subagent/message'
  agent_id?: string
  description?: string
  status?: string
  result?: string
  error?: string | null
  [k: string]: unknown
}
export interface CompactionEvent {
  type: 'compaction/start' | 'compaction/end' | 'compaction/prune' | 'compaction/progress'
  chars_removed?: number
  pruned?: number
  [k: string]: unknown
}
export interface QueueStateEvent {
  type: 'queue_state'
  session_id?: string
  pending?: number
  running?: boolean
  paused?: boolean
  pause_reason?: string
  waiting_reason?: '' | 'environment'
}
export interface PlanModeEvent { type: 'plan_mode'; active?: boolean; [k: string]: unknown }
export interface CancelledEvent { type: 'cancelled'; cancelled?: boolean; [k: string]: unknown }
export interface QuestionAnsweredEvent {
  type: 'question_answered'
  question_id?: string
  answers?: unknown
  [k: string]: unknown
}
export interface ErrorEvent { type: 'error'; message?: string; error?: string; code?: string; [k: string]: unknown }
export interface DoneEvent { type: 'done'; [k: string]: unknown }
export interface ResumedEvent { type: 'resumed'; session_id?: string; last_seq?: number; [k: string]: unknown }

export type StreamEvent =
  | ReadyEvent
  | UserMessageEvent
  | AssistantDeltaEvent
  | AssistantMessageEvent
  | ToolStartEvent
  | ToolOutputEvent
  | ToolEndEvent
  | TodoWriteEvent
  | SkillLoadedEvent
  | SubagentStatusEvent
  | SubagentMessageEvent
  | CompactionEvent
  | QueueStateEvent
  | PlanModeEvent
  | CancelledEvent
  | QuestionAnsweredEvent
  | ErrorEvent
  | DoneEvent
  | ResumedEvent
  | ({ type: string } & Record<string, unknown>)

/* ============================ 前端内部模型 ============================ */

export type BlockKind =
  | 'hook'
  | 'deliverables'
  | 'user'
  | 'assistant'
  | 'thinking'
  | 'tool'
  | 'todo'
  | 'question'
  | 'compaction'
  | 'subagent'
  | 'team'
  | 'notice'
  | 'error'
  | 'skill'
  | 'turn'

export interface HookBlock extends BlockBase {
  kind: 'hook'
  hooks: HookActivity[]
}

export interface ToolBlockState {
  contentRef?: ToolResultReference
  /** The UI settled a missing terminal result; a real result may still replace it. */
  outcomePending?: boolean
  hooks?: HookActivity[]
  sourceSeq?: number
  kind: 'tool'
  id: string
  at: number
  turn?: number
  callId: string
  name: string
  status: 'running' | 'ok' | 'error'
  input?: unknown
  output?: unknown
  outputKey?: string
  display?: unknown
  isError?: boolean
  durationMs?: number
  startedAt?: number
  progress?: ToolOutputPreview
}

export interface ToolOutputPreview {
  encodingErrors?: import('@/lib/protocol.generated').EncodingError[]
  seq: number
  stdout: string
  stderr: string
  stdoutEnd: number
  stderrEnd: number
  stdoutDropped: number
  stderrDropped: number
}

export interface BlockBase {
  sourceSeq?: number
  id: string
  kind: BlockKind
  at: number
  turn?: number
}

export interface UserBlock extends BlockBase {
  requester?: import('@/lib/protocol.generated').HumanRequester
  goalId?: string
  teamRecipients?: import('@/lib/teamAuthor').TeamMessageRecipients
  kind: 'user'
  text: string
  requestId?: string
  steeringStatus?: 'pending' | 'applied' | 'unapplied'
  steeringTeamDelivered?: boolean
  steeringDeliverySeq?: number | null
  steeringReason?: string
  /** 随这条消息发出的附件（服务端已落盘，id 为内容 sha256）。 */
  attachments?: AttachmentInfo[]
}
export interface AssistantBlock extends BlockBase {
  speaker?: import('@/lib/agentSpeaker').AgentSpeaker
  previewOnly?: boolean
  previewTruncated?: boolean
  kind: 'assistant'
  author?: import('@/lib/teamAuthor').TeamAuthorPresentation
  partial?: 'stopped' | 'failed' | 'interrupted'
  checkpointGap?: boolean
  text: string
  streaming?: boolean
  usage?: TokenUsage | null
  model?: string | null
  error?: boolean
}
export interface ThinkingBlock extends BlockBase {
  speaker?: import('@/lib/agentSpeaker').AgentSpeaker
  author?: import('@/lib/teamAuthor').TeamAuthorPresentation
  kind: 'thinking'
  partial?: 'stopped' | 'failed' | 'interrupted'
  checkpointGap?: boolean
  text: string
  streaming?: boolean
}
export interface TodoBlock extends BlockBase { kind: 'todo'; todos: TodoItem[] }
export interface CompactionBlock extends BlockBase {
  kind: 'compaction'
  error?: string
  skipped?: boolean
  cancelled?: boolean
  compactionId?: number
  completedChunks?: number
  totalChunks?: number
  stage?: string
  mergeLevel?: number
  currentChunk?: number
  attempt?: number
  maxAttempts?: number
  retryInSeconds?: number
  pruned?: number
  charsRemoved?: number
  phase: 'start' | 'end'
  summary?: string
}
export interface SubagentBlock extends BlockBase {
  kind: 'subagent'
  agentId: string
  description: string
  status: string
  result?: string
  error?: string | null
  durationMs?: number | null
  toolCalls?: number
}
/** 团队进度卡片里的一个成员状态（来自 agent/team 事件快照）。 */
export interface TeamMemberState {
  displayName?: string
  runner?: 'codex' | 'claude' | 'opencode'
  agent?: string
  context_session_id?: string
  agent_id?: string
  planning_agent_id?: string
  name: string
  role: string
  status: string
  error?: string | null
  depends_on?: string[]
  delivery?: { tree: string; commit?: string }
  delivery_inputs?: { tree: string; sources: Array<{ id: string; tree: string }> }
  handoff_error?: { code?: string; message: string; conflicts: string[] }
}
/** 团队执行进度：start → plan → execute → summary → done/failed。 */
export interface TeamBlock extends BlockBase {
  direct?: boolean
  publicReply?: { member: string; displayName: string; agent?: string; agentId: string; requestId: string; text: string; stage: 'reply' | 'summary'; truncated: boolean }
  previews?: Record<string, { agentId: string; requestId: string; version: number; text: string; status: 'streaming' | 'cleared'; stage?: 'member' | 'reply' | 'summary' | 'planning'; thinking?: string; thinkingTruncated?: boolean; truncated: boolean }>
  kind: 'team'
  displayName?: string
  decision?: 'reply' | 'delegate'
  decisionReason?: string
  runId: string
  workspaces?: Record<string, string>
  worktrees?: Array<{ id: string; path: string; team_member: string; branch?: string; source_snapshot?: {tree: string; status: 'pending' | 'preparing' | 'ready' | 'failed'} }>
  assignments?: Record<string, string>
  recovered?: boolean
  team: string
  phase: 'start' | 'plan' | 'execute' | 'summary' | 'done' | 'failed' | 'interrupted'
  task?: string
  members: TeamMemberState[]
  summary?: string
  error?: string
  fallback?: boolean
  retried?: boolean
}
export interface ApprovalNotice { decisions: unknown[]; reviewer?: unknown; reason?: unknown; tool: string }
export type ResourceCopy = import('./lib/protocol.generated').ResourceCopy
export interface ResourceTemplate { template: { key: string; params: Record<string, ResourceText | number> } }
export type ResourceText = string | ResourceCopy | ResourceTemplate
export type NoticeText = ResourceText | { approval: ApprovalNotice }
export interface NoticeBlock extends BlockBase { kind: 'notice'; text: NoticeText; tone: 'info' | 'warn' }
export interface ErrorBlock extends BlockBase {
  kind: 'error'
  message: string
  details?: unknown
  messageCopy?: ResourceCopy
  messageKey?: string
  code?: string
  turn?: number
  historical?: boolean
}
export interface SkillBlock extends BlockBase { kind: 'skill'; name?: string }
export interface TurnBlock extends BlockBase {
  kind: 'turn'
  historical?: boolean
  phase: 'start' | 'end'
  turn?: number
  reason?: string
  usage?: TokenUsage | null
}
export interface QuestionBlock extends BlockBase {
  kind: 'question'
  questionId: string
  questions: QuestionItem[]
  answered?: boolean
  closed?: boolean
  answers?: unknown
}

/** 回合结束时 present 工具声明的交付物（ui-deliverables）。 */
export interface DeliverablesBlock extends BlockBase {
  kind: 'deliverables'
  files: DeliverableFile[]
  workspaceRoot?: string | null
}

export interface ConversationSearchHit {
  source_seq: number
  source_time: number
  role: 'user' | 'assistant'
  prefix: string
  match: string
  suffix: string
  start_char: number
}
export interface ConversationSearchResult {
  session_id: string
  query: string
  through: number
  total: number
  results: ConversationSearchHit[]
  has_more: boolean
  next_before: number | null
}
export interface ConversationMessagePart {
  session_id: string
  source_seq: number
  source_time: number
  role: 'user' | 'assistant'
  text: string
  start: number
  end: number
  total_chars: number
  previous_start: number | null
  next_start: number | null
}

export type Block =
  | HookBlock
  | DeliverablesBlock
  | UserBlock
  | AssistantBlock
  | ThinkingBlock
  | ToolBlockState
  | TodoBlock
  | CompactionBlock
  | SubagentBlock
  | TeamBlock
  | NoticeBlock
  | ErrorBlock
  | SkillBlock
  | TurnBlock
  | QuestionBlock

export type ConnectionState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

export type InspectorTab = `member:${string}` | 'home' | 'academy' | 'browser' | 'runtime' | 'terminal' | 'todos' | 'context' | 'files' | 'prompts' | 'queue' | 'changes' | 'git'

/* ==================== 运行时可观测面板 ====================

   模型能起常驻终端、派子代理、开后台作业，但对话流里看不到它们的中间态。
   这三个形状直接对齐后端 `/api/v1/runtime`：常驻终端 / 子代理 / 后台作业。
   刻意保持 snake_case 与后端一致（这些字段几乎全是后端对象直接序列化来的），
   只把 `sessionId` 这种 camelCase 键留给 HTTP/WebSocket 协议层。 */

/** 常驻终端的生命周期状态。运行中是 running，退出后带退出码/信号。 */
export interface TerminalStatus {
  kind: 'running' | 'exited'
  exit_code?: number | null
  signal?: number | null
}

export interface RuntimeTerminal extends Pick<import('@/lib/protocol.generated').TerminalView, 'output_encoding' | 'encoding_errors' | 'output_error'> {
  /** 终端自己的会话 id（不是 owner 会话 id），用作 /output 的路径参数。 */
  sessionId: string
  type: string
  cwd: string
  pid: number
  status: TerminalStatus
  name?: string
  transport?: 'pty' | 'pipe'
  control?: { state: 'stopping' | 'cleanup-failed'; error?: string }
}

export type SubagentStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'interrupted'

export interface RuntimeSubagent {
  agent_id: string
  description: string
  status: SubagentStatus
  parent_session_id: string
  child_session_id?: string | null
  parent_agent_id?: string | null
  root_session_id?: string | null
  context_persisted?: boolean
  created_at: number
  duration_ms: number | null
  result?: string | null
  error?: string | null
  tool_calls: number
  pending_messages: number
  result_truncated?: boolean
  archived?: boolean
  recovered?: boolean
  archive_error?: string
  error_copy?: ResourceCopy
  error_parts?: import('./lib/protocol.generated').SubagentDetail['error_parts']
  archive_error_copy?: ResourceCopy
}

export type SubagentDetailPayload = import('@/lib/protocol.generated').SubagentDetailResponse

export interface RuntimeJob extends Pick<import('@/lib/protocol.generated').JobView, 'output_encoding' | 'encoding_errors'> {
  completionScope?: 'terminal-wait'
  terminalId?: string
  terminalStatus?: TerminalStatus
  wait?: { reason: string; detail?: string }
  id: string
  kind: string
  label: string
  status: string
  startedAt: number
  exitCode?: number
  archived?: boolean
  recovered?: boolean
  lastStatus?: string
  archiveError?: string
  detail?: string
  finishedAt?: number
  cleanupError?: string
  scope?: 'foreground-cleanup'
  callId?: string
  turn?: number
  step?: number
  runId?: string
  pid?: number
}

export interface JobOutputPayload {
  job: RuntimeJob
  stdout: string
  stderr: string
  stdout_offset: number
  stderr_offset: number
  stdout_end: number
  stderr_end: number
  truncated: boolean
}

/** 一次 `/api/v1/runtime` 的合并快照：三类状态 + 归属会话。 */
export interface RuntimePayload {
  sessionId: string | null
  terminals: RuntimeTerminal[]
  subagents: RuntimeSubagent[]
  subagents_has_more?: boolean
  subagents_next_before?: string | null
  jobs: RuntimeJob[]
  jobs_has_more?: boolean
  jobs_next_before?: string | null
  jobs_history_error?: string
}

/** 一段终端输出的游标窗口。 */
export interface TerminalOutputPayload {
  text: string
  totalLines: number
  lineBegin: number
  lineEnd: number
  truncated: boolean
  sessionId: string
  session: TerminalStatus
}

export interface TerminalTailPayload extends TerminalOutputPayload {
  pendingText: string
  partialFirstLine: boolean
  terminal: RuntimeTerminal
}

export interface TerminalStopPayload {
  sessionId: string
  outcome: 'closed' | 'already-closing' | 'cleanup-failed'
  terminal: RuntimeTerminal
  cleanupError?: string
  audit_error?: string
}

/** 服务端已落盘的附件。`attachmentId` 是内容 sha256，永不变化。 */
export interface AttachmentInfo {
  attachmentId: string
  name: string
  mediaType: string
  size: number
  isImage: boolean
}

/** 附件仓库统计，用于"本地已缓存 N 个文件"这类提示。 */
export interface AttachmentStats {
  count: number
  totalBytes: number
  root: string
}

export type {
  AcademyCourse, AcademyCourseSummary, AcademyLesson, AcademyProgress, AcademyProject,
  AcademySourceFile, AcademyRun, AcademyTraceEvent, AcademyReceipt, AcademyCheckpoint,
  AcademyRestorePreview, AcademyFile, AcademySubmission, AcademyAssessmentDimension, AcademyAssessment,
  AcademyKnowledgeCheck, AcademyDesign, AcademyModelRun, AcademyTutorTurn, AcademyTutorProposal,
} from './lib/protocol.generated'

export type AcademyDimensionId = import('./lib/protocol.generated').AcademyAssessmentDimension['id']
