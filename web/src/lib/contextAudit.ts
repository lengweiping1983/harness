import { t } from '@/i18n'
import type { RequestTrace } from './requestTraces'
import type { ContextStatistics, ToolResultProjectionSummary } from './protocol.generated'

export interface SkillAuditPage {
  through: number
  skills: { id: number; time: number; name: string; status: 'loaded' | 'failed' | 'unconfirmed'; context: 'full' | 'pruned' | 'absent'; result_seq: number | null; description: string; error: string }[]
  has_more: boolean
  next_before: number | null
}

export interface CompactionCapacity {
  version: 1
  message_tokens: number
  runtime_tokens: number
  tool_tokens: number
  input_tokens: number
  context_window: number | null
  compact_threshold: number | null
  remaining_tokens: number | null
}

export interface CompactionAudit {
  id: number
  time: number
  turn?: number
  status: 'running' | 'pruned' | 'failed' | 'skipped' | 'completed' | 'cancelled'
  before_approx_tokens?: number
  after_approx_tokens?: number
  capacity_before?: CompactionCapacity
  capacity_after?: CompactionCapacity
  trigger?: 'threshold' | 'forced'
  completed_chunks?: number
  total_chunks?: number
  stage?: string
  merge_level?: number
  current_chunk?: number
  attempt?: number
  max_attempts?: number
  retry_in_seconds?: number
  pruned_results: number
  chars_removed: number
  summary_seq?: number
  summary_preview?: string
  shadowed_count?: number
  retained_nodes?: number
  policy_version?: number
  source_first?: number
  source_last?: number
  error?: string | null
}
export interface ContextAudit {
  session_id: string
  through: number
  current: {
    through: number
    event_count: number
    surface_nodes: number
    message_count: number
    tool_result_projection?: ToolResultProjectionSummary
    roles: Record<string, number>
    latest_request: RequestTrace | null
    latest_reported_request: RequestTrace | null
    statistics: ContextStatistics
  } | null
  compactions: CompactionAudit[]
  has_more: boolean
  next_before: number | null
}

export const COMPACTION_STATUS: Record<CompactionAudit['status'], string> = {
  get running() { return t("ui.lib.contextAudit.231c6b8902d9") }, get pruned() { return t("ui.lib.contextAudit.c509c97df283") }, get failed() { return t("ui.lib.contextAudit.1d8961c7743a") }, get skipped() { return t("ui.components.chat.Conversation.71e95b289aad") }, get completed() { return t("ui.lib.contextAudit.e28ee54a9338") },
  get cancelled() { return t("ui.lib.contextAudit.58a534f6a6b3") },
}
export const CONTEXT_ROLE: Record<string, string> = Object.setPrototypeOf({
  get SystemMessage() { return t("ui.lib.requestTraces.034d7f47e6e5") }, get DeveloperMessage() { return t("ui.lib.contextAudit.2f6dd2b1311e") }, get UserMessage() { return t("ui.lib.contextAudit.e6be19b38c9e") },
  get AssistantMessage() { return t("ui.lib.requestTraces.a7685e43f606") }, get ToolResultMessage() { return t("ui.lib.contextAudit.e829d982e777") },
}, null)

export function mergeCompactions(previous: CompactionAudit[], incoming: CompactionAudit[]) {
  const rows = new Map(previous.map((row) => [row.id, row]))
  for (const row of incoming) rows.set(row.id, row)
  return [...rows.values()].sort((a, b) => a.id - b.id)
}
