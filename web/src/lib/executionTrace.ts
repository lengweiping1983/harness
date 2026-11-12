import type { RequestTrace } from './requestTraces'
export interface TraceAgent {
  agent_id: string
  child_session_id?: string | null
  description: string
  status: string
}
export interface TraceEntry {
  id: string
  kind: 'request' | 'tool' | 'agent'
  time: number
  order: number
  name: string
  description?: string
  status: string
  call_id?: string
  run_id?: string | null
  run_attribution_ambiguous?: boolean
  duration_ms?: number | null
  request_id?: string | null
  trace_id?: string | null
  request?: RequestTrace
  agent?: TraceAgent
}
export interface ExecutionTracePage {
  entries: TraceEntry[]
  has_more: boolean
  next_after: string | null
  session_id: string
  context_missing: boolean
}
