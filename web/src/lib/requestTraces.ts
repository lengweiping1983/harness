import type {ResourceCopy} from '@/types'
import { t } from '@/i18n'
import type { RequestContentComposition, RequestPromptSource, RequestToolDefinition, RequestToolSource } from './protocol.generated'
export interface RequestTrace {
  id: string
  seq: number
  session_id: string
  parent_session_id: string | null
  request_id: string
  purpose: string
  time: number
  model: string
  endpoint: string
  status: string
  body_chars: number
  body_bytes: number
  message_count: number
  tool_count: number
  body_redacted?: boolean
  body_hash: string
  context_checkpoint?: { event_seq: number; event_count: number; surface_nodes: number;
    system_seq: number | null; request_header_seq: number | null; compaction_summary_seq: number | null }
  usage?: import('@/types').TokenUsage | null
  observation?: { duration_ms: number; first_event_ms: number | null; event_count: number;
    terminal_reason: string; error_type?: string | null; error_code?: string | null;
    text_diagnostic?: { field: string; position: number; codepoint: string; context: string; input_contains_same_fragment: boolean } | null }
}
export interface RequestTraceDetail {
  composition: RequestContentComposition
  request: RequestTrace
  messages: { index: number; role: string; chars: number; preview: string;
    tool_sources?: RequestToolSource[]; prompt_sources?: RequestPromptSource[]; prompt_source_count?: number }[]
  tool_names: string[]
  tool_definitions: RequestToolDefinition[]
  parameters: Record<string, unknown>
  cache_comparison?: {
    previous_id: string
    shared_messages: number
    shared_prefix_chars: number
    first_changed_message: number | null
    changes: ('endpoint' | 'model' | 'tools' | 'settings')[]
    sequence_change?: 'modified' | 'appended' | 'truncated' | 'identical'
    first_difference?: { path: string; kind: string; text_offset?: number } | null
    changed_settings: string[]
  } | null
}
export interface RequestTracePart { text: string; offset: number; total_chars: number; next_offset: number | null }

export const REQUEST_ROLE: Record<string, string> = Object.setPrototypeOf({
  get system() { return t("ui.lib.requestTraces.034d7f47e6e5") }, get developer() { return t("ui.lib.requestTraces.e6e6e10ae4af") }, get user() { return t("ui.lib.requestTraces.a7dfeaf0c12c") }, get assistant() { return t("ui.lib.requestTraces.a7685e43f606") }, get tool() { return t("ui.lib.requestTraces.d7fea1743a9d") },
}, null)
export const REQUEST_STATUS: Record<string, string> = Object.setPrototypeOf({
  get started() { return t("ui.lib.requestTraces.cc1f354b0df6") }, get completed() { return t("ui.components.inspector.ContextAuditPanel.f28461bb49c8") }, get failed() { return t("ui.components.chat.Conversation.28384d7afd2e") }, get interrupted() { return t("ui.member.status.interrupted") },
}, null)

export const REQUEST_TERMINAL_REASON: Record<string, string> = Object.setPrototypeOf({
  get done() { return t("ui.lib.requestTraces.7809a5cd638a") }, get cancelled() { return t("ui.lib.requestTraces.a3e5c2a688bf") }, get consumer_closed() { return t("ui.lib.requestTraces.aa1e88d5332c") },
  get stream_ended() { return t("ui.lib.requestTraces.5313f337a571") }, get error() { return t("ui.lib.requestTraces.baedc8bf42be") },
}, null)

/** 完整模板决定路径、偏移和字段顺序提示的标点及空格。 */
export function requestDifferenceCopy(difference: {path: string; kind: string; text_offset?: number}): ResourceCopy {
  const offset = difference.text_offset !== undefined
  const order = difference.kind === 'key_order'
  return {key: order ? offset ? 'ui.requestStats.changeTextOrderLocation' : 'ui.requestStats.changeOrderLocation'
    : offset ? 'ui.requestStats.changeTextLocation' : 'ui.requestStats.changeLocation',
    params: {path: difference.path, offset: difference.text_offset ?? ''}}
}
