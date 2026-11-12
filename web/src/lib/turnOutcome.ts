import { hasTranslation, t } from '@/i18n'
import type { ErrorBlock, ResourceCopy, SessionEvent, SessionMessage, TurnBlock } from '@/types'

export type TurnOutcomeBlock = ErrorBlock | TurnBlock

/** 只合并同轮、同码及完整诊断一致的错误，不按标题或前缀近似匹配。 */
export function sameTurnFailure(left: ErrorBlock, right: ErrorBlock): boolean {
  if (left.turn !== right.turn || left.code !== right.code || left.messageKey !== right.messageKey) return false
  if (left.details !== undefined && right.details !== undefined && JSON.stringify(left.details) !== JSON.stringify(right.details)) return false
  return left.message === right.message || left.code !== undefined &&
    (left.message === `[${left.code}] ${right.message}` || right.message === `[${left.code}] ${left.message}`)
}

const OUTCOMES: Record<string, { title: string; hint: string }> = {
  aborted: { get title() { return t("ui.components.chat.blocks.MessageBlocks.f006455e3baf") }, get hint() { return t("ui.lib.turnOutcome.cb158d172dfa") } },
  interrupted: { get title() { return t("ui.lib.turnOutcome.3cd1e27e45c8") }, get hint() { return t("ui.lib.turnOutcome.134c08d88971") } },
  blocked: { get title() { return t("ui.lib.turnOutcome.ded5e2c485fb") }, get hint() { return t("ui.lib.turnOutcome.063b2e4ac9db") } },
  'max-tokens': { get title() { return t("ui.lib.turnOutcome.f5f8c677a79f") }, get hint() { return t("ui.lib.turnOutcome.97201bd43764") } },
  'max-steps': { get title() { return t("ui.lib.turnOutcome.2dfdeb7abb7c") }, get hint() { return t("ui.lib.turnOutcome.97201bd43764") } },
  budget: { get title() { return t("ui.lib.turnOutcome.f64f29723417") }, get hint() { return t("ui.lib.turnOutcome.97201bd43764") } },
}

export function turnOutcomeCopy(reason?: string) { return reason && Object.hasOwn(OUTCOMES, reason) ? OUTCOMES[reason] : null }

/** 只接受明确且浏览器可读的资源身份，外部诊断不按原文翻译。 */
export function runErrorCopy(error: Record<string, unknown> | null): ResourceCopy | undefined {
  const details = error?.details && typeof error.details === 'object' ? error.details as Record<string, unknown> : null
  const value = error?.copy ?? details?.copy
  if (!value || typeof value !== 'object' || Array.isArray(value)) return
  const copy = value as Record<string, unknown>
  if (typeof copy.key !== 'string' || !hasTranslation(copy.key)) return
  if (copy.params !== undefined && (!copy.params || typeof copy.params !== 'object' || Array.isArray(copy.params)
    || !Object.values(copy.params).every(value => typeof value === 'string' || typeof value === 'number' && Number.isFinite(value)))) return
  return {key: copy.key, ...(copy.params !== undefined ? {params: {...copy.params as ResourceCopy['params']}} : {})}
}

/** Terminal evidence comes from committed turn/end, never an optimistic UI flag. */
export function turnFailure(data: Record<string, unknown>, id: string, at: number): ErrorBlock | null {
  const reason = data.reason
  const terminal = reason && typeof reason === 'object' ? reason as Record<string, unknown> : null
  if ((terminal?.kind ?? reason) !== 'error') return null
  const error = terminal?.error && typeof terminal.error === 'object' ? terminal.error as Record<string, unknown>
    : typeof data.message === 'string' ? data : null
  const message = [error?.message, terminal?.message, data.error].find((value) => typeof value === 'string' && value.trim()) as string | undefined
  const text = message ?? ''
  const code = typeof error?.code === 'string' ? error.code : /^\[([^\]]+)\]/.exec(text)?.[1]
  return { id, kind: 'error', at, message: text, details: error?.details, messageCopy: runErrorCopy(error), messageKey: message == null ? 'ui.lib.turnOutcome.ac8aaa680b6c' : undefined, code, turn: typeof data.turn === 'number' ? data.turn : undefined }
}

export function turnOutcome(data: Record<string, unknown>, id: string, at: number): TurnOutcomeBlock | null {
  const failure = turnFailure(data, id, at)
  if (failure) return failure
  const reason = data.reason
  const raw = reason && typeof reason === 'object' ? (reason as Record<string, unknown>).kind : reason
  const kind = raw === 'cancelled' ? 'aborted' : raw
  if (typeof kind !== 'string' || !turnOutcomeCopy(kind)) return null
  return { id, kind: 'turn', phase: 'end', at, reason: kind, turn: typeof data.turn === 'number' ? data.turn : undefined }
}

/** Buckets keep message order, including compaction and outcome-only pages. */
export function outcomeBuckets(messages: SessionMessage[], events: SessionEvent[]): Map<number, TurnOutcomeBlock[]> {
  const buckets = new Map<number, TurnOutcomeBlock[]>(), seen = new Set<number>()
  const users = messages.map((message, index) => ({ message, index })).filter(({ message }) => message.role === 'user' && message.source !== 'compaction')
  const linked = users.length > 0 && users.every(({ message }, index) => Number.isSafeInteger(message.source_seq) && message.source_seq! >= 0 &&
    (index === 0 || message.source_seq! > users[index - 1]!.message.source_seq!))
  for (const [ordinal, event] of events.entries()) {
    if (event.type !== 'turn/end' || !event.data) continue
    const seq = Number.isSafeInteger(event.seq) && event.seq! >= 0 ? event.seq! : undefined
    if (seq != null && seen.has(seq)) continue
    if (seq != null) seen.add(seq)
    const block = turnOutcome(event.data, 'turn-outcome:' + (seq ?? 'local-' + ordinal) + ':' + (event.data.turn ?? ''),
      typeof event.time === 'number' && Number.isFinite(event.time) ? event.time : Date.now())
    if (!block) continue
    if (block.kind === 'error') block.id = block.id.replace(/^turn-outcome:/, 'turn-error:')
    block.sourceSeq = seq
    let index = messages.length
    if (linked && seq != null) {
      let low = 0, high = users.length
      while (low < high) {
        const middle = Math.floor((low + high) / 2)
        if (users[middle]!.message.source_seq! <= seq) low = middle + 1
        else high = middle
      }
      index = users[low]?.index ?? messages.length
      block.historical = low === 0
    } else block.historical = true
    const bucket = buckets.get(index) ?? []
    bucket.push(block)
    buckets.set(index, bucket)
  }
  return buckets
}
