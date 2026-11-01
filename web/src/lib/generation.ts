import { formatElapsedDuration } from './utils'
import { t } from '@/i18n'
import type {ResourceCopy} from '@/types'
export type GenerationPhase = 'waiting' | 'generating' | 'thinking' | 'writing' | 'retrying'
export interface GenerationState {
  request_id: string
  phase: GenerationPhase
  model: string
  attempt: number | null
  max_attempts: number | null
  max_tokens: number | null
  requested_max_tokens?: number | null
  context_window?: number | null
  model_max_output_tokens?: number | null
  adjustments?: ResourceCopy[]
  network_timeout_seconds?: number | null
  network_bytes?: number
  network_idle_ms?: number
  progress_idle_ms?: number
  elapsed_ms: number
  idle_ms: number
  retry_in_seconds: number
  text_chars: number
  thinking_chars: number
  receivedAt: number
}

const phases = new Set(['waiting', 'generating', 'thinking', 'writing', 'retrying'])
const positive = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
const count = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : 0
const milliseconds = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0
const integer = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : null

/** Anchor server elapsed time to a local monotonic clock, never a wall-clock offset. */
export function readGeneration(raw: unknown, now = performance.now()): GenerationState | null {
  if (!raw || typeof raw !== 'object') return null
  const data = raw as Record<string, unknown>
  if (typeof data.request_id !== 'string' || !data.request_id || !phases.has(String(data.phase))) return null
  return { request_id: data.request_id, phase: data.phase as GenerationPhase,
    model: typeof data.model === 'string' ? data.model : '',
    attempt: integer(data.attempt), max_attempts: integer(data.max_attempts),
    max_tokens: integer(data.max_tokens),
    requested_max_tokens: integer(data.requested_max_tokens), context_window: integer(data.context_window), model_max_output_tokens: integer(data.model_max_output_tokens), adjustments: Array.isArray(data.adjustments) ? data.adjustments.filter((v): v is ResourceCopy => v !== null && typeof v === 'object' && !Array.isArray(v) && typeof v.key === 'string' && (v.params === undefined || v.params !== null && typeof v.params === 'object' && !Array.isArray(v.params) && Object.values(v.params).every(value => typeof value === 'string' || typeof value === 'number' && Number.isFinite(value)))) : [],
    network_timeout_seconds: positive(data.network_timeout_seconds),
    network_bytes: count(data.network_bytes), network_idle_ms: milliseconds(data.network_idle_ms), progress_idle_ms: milliseconds(data.progress_idle_ms),
    elapsed_ms: milliseconds(data.elapsed_ms), idle_ms: milliseconds(data.idle_ms),
    retry_in_seconds: positive(data.retry_in_seconds) ?? 0,
    text_chars: count(data.text_chars), thinking_chars: count(data.thinking_chars), receivedAt: now }
}

export function generationTime(state: GenerationState, now = performance.now()) {
  const delta = Math.max(0, now - state.receivedAt)
  return { elapsed_ms: state.elapsed_ms + delta, idle_ms: state.idle_ms + delta }
}

export function generationHealthTime(state: GenerationState, now = performance.now()) {
  const delta = Math.max(0, now - state.receivedAt)
  return { network_idle_ms: (state.network_idle_ms ?? 0) + delta,
    progress_idle_ms: (state.progress_idle_ms ?? 0) + delta }
}

export function updateGeneration(state: GenerationState, update: Record<string, unknown>, now = performance.now()) {
  return readGeneration({ ...state, ...generationTime(state, now), ...generationHealthTime(state, now), ...update }, now)
}

export function generationDelta(state: GenerationState, delta: string, thinking: boolean, now = performance.now()) {
  return updateGeneration(state, { idle_ms: 0,
    phase: thinking ? 'thinking' : 'writing',
    text_chars: state.text_chars + (thinking ? 0 : Array.from(delta).length),
    thinking_chars: state.thinking_chars + (thinking ? Array.from(delta).length : 0) }, now)
}

export function generationLabel(state: GenerationState, connected: boolean, stopping: boolean) {
  if (!connected) return t("ui.lib.generation.37983104760a")
  if (stopping) return t("ui.lib.generation.af48159f380f")
  return ({ waiting: t("ui.lib.generation.a304ae4ae47c"), generating: t("ui.lib.generation.74224d9bc93e"), thinking: t("ui.lib.generation.a82ee33df290"),
    writing: t("ui.lib.generation.a79570be8d54"), retrying: t("ui.lib.generation.2575219eec28") })[state.phase]
}

export function secondsLabel(milliseconds: number) {
  return formatElapsedDuration(Math.max(0, milliseconds)) ?? '—'
}
