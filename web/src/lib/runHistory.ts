import { formatElapsedDuration } from './utils'
import { ResourceError } from './resourceError'
import { t } from '@/i18n'
import type { ContinueRun, RecoveryPreview, RunRecord, SubmissionStatus } from './protocol.generated'
import { runtimeRequest, ApiError } from './http'

export const RUN_STATUS = { get queued() { return t("ui.lib.runHistory.117c113c3b45") }, get running() { return t("ui.components.chat.blocks.ToolBlock.5026a63b58cd") }, get stopping() { return t("ui.lib.runHistory.af48159f380f") }, get completed() { return t("ui.lib.runHistory.c0b3fbff51cc") }, get failed() { return t("ui.components.chat.Conversation.28384d7afd2e") }, get interrupted() { return t("ui.lib.runHistory.f9d19345a067") } } as const

export const isRemoved = (record: RunRecord) => record.status === 'interrupted' && ['run/removed', 'run/steered'].includes(String(record.error?.code))

export function taskSummary(record: RunRecord): string {
  const text = record.task_text ?? record.text
  return record.retry_of && record.task_text ? t("ui.lib.runHistory.3a3f61997bf7") + text : text
}

export function runStatus(record: RunRecord, context?: { stopping: boolean; waiting: 'answer' | 'approval' | null }): string {
  if (record.error?.code === 'run/steered') return t("ui.lib.runHistory.040892dd614e")
  if (isRemoved(record)) return t("ui.lib.runHistory.174688c755b9")
  if (record.status === 'running' && context) {
    if (context.stopping) return t("ui.lib.runHistory.af48159f380f")
    if (context.waiting) return context.waiting === 'approval' ? t("ui.lib.runHistory.150fec9297f2") : t("ui.lib.runHistory.ed16bd1db4b6")
  }
  if (record.status === 'interrupted' && (record.result?.reason === 'aborted' || record.error?.code === 'run/cancelled' || !record.error)) return t('ui.components.chat.blocks.MessageBlocks.f006455e3baf')
  return RUN_STATUS[record.status]
}

export function runDuration(record: RunRecord): string | null {
  if (record.started_at == null || record.finished_at == null) return null
  const seconds = record.finished_at - record.started_at
  if (!Number.isFinite(seconds) || seconds < 0) return null
  if (seconds < 1) return t("ui.lib.runHistory.ef756aef8083")
  return formatElapsedDuration(seconds * 1000)
}

export function continuationKey(runId: string, mode: 'inspect' | 'continue' = 'continue'): string {
  return mode + ':' + encodeURIComponent(runId)
}

/** 恢复页绑定原运行和完整范围；同版本分页不能撤销核实要求或重复证据。 */
export async function readRecoveryPreview(sessionId: string, runId: string, signal: AbortSignal, previous?: RecoveryPreview): Promise<RecoveryPreview> {
  const offset = previous?.next_offset ?? 0
  const page = await runtimeRequest<RecoveryPreview>('/sessions/' + encodeURIComponent(sessionId) + '/runs/' + encodeURIComponent(runId) + '/recovery' + (previous ? '?offset=' + offset : ''), 'GET', undefined, {signal})
  if (page.session_id !== sessionId || page.run_id !== runId) throw new ResourceError({key: 'ui.http.invalidResponse'})
  if (previous && page.version !== previous.version) throw new ResourceError({key: 'ui.components.inspector.RecoveryReview.ad2c90cbaebc'})
  const end = offset + page.tools.length
  const tools = [...(previous?.tools ?? []), ...page.tools]
  const identities = tools.map(tool => JSON.stringify([tool.source_seq, tool.call_id]))
  const observedUncertain = tools.filter(tool => tool.effect !== 'read' && ['unknown', 'reported_error'].includes(tool.status)).length
  if (!Number.isSafeInteger(page.total_tools) || page.total_tools < end ||
      !Number.isSafeInteger(page.uncertain_operations) || page.uncertain_operations < observedUncertain || page.uncertain_operations > page.total_tools ||
      typeof page.scope_known !== 'boolean' || page.verification_required !== (!page.scope_known || page.uncertain_operations > 0) ||
      (previous && (page.total_tools !== previous.total_tools || page.scope_known !== previous.scope_known ||
        page.uncertain_operations !== previous.uncertain_operations || page.verification_required !== previous.verification_required)) ||
      new Set(identities).size !== tools.length ||
      (end === page.total_tools && page.uncertain_operations !== observedUncertain) ||
      (end < page.total_tools && end === offset) ||
      page.next_offset !== (end < page.total_tools ? end : null)) throw new ResourceError({key: 'ui.http.invalidResponse'})
  return page
}

export type RecoveryDraft = Required<Pick<ContinueRun, 'mode' | 'text' | 'recovery_version' | 'verification_note'>>

/** 每个失败执行对应一个继续意图；跨标签页和刷新先核实已有提交，不自动重放。 */
export async function continueRun(sessionId: string, originalId: string, draft?: RecoveryDraft): Promise<RunRecord> {
  const key = continuationKey(originalId, draft?.mode)
  function checked(run: RunRecord): RunRecord {
    if (run.session_id !== sessionId || run.retry_of !== originalId) {
      throw new ApiError({ok: false, message: t("ui.lib.runHistory.9836a9ec6624"), copy: {key: "ui.lib.runHistory.9836a9ec6624"}, code: 'run/idempotency-conflict', status: 409})
    }
    if (draft && (run.recovery?.mode !== draft.mode || run.recovery.source_version !== draft.recovery_version ||
        run.recovery.verification_note !== draft.verification_note.trim() ||
        run.recovery.instruction_text !== draft.text)) {
      throw new ApiError({ok: false, message: t("ui.lib.runHistory.052b2e1a4969"), copy: {key: "ui.lib.runHistory.052b2e1a4969"}, code: 'run/idempotency-conflict', status: 409})
    }
    return run
  }
  const known = await runtimeRequest<SubmissionStatus>('/sessions/' + sessionId + '/submissions/' + encodeURIComponent(key))
  if (known.run) {
    return checked(known.run)
  }
  try {
    return checked(await runtimeRequest<RunRecord>('/sessions/' + sessionId + '/continue', 'POST', { run_id: originalId, ...draft },
      { headers: { 'Idempotency-Key': key, 'X-Request-ID': key } }))
  } catch (error) {
    if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) throw error
    // 回执丢失时只查询，不再次 POST。显式重试仍使用原标识。
    let recovered: RunRecord | null = null
    try {
      const receipt = await runtimeRequest<SubmissionStatus>('/sessions/' + sessionId + '/submissions/' + encodeURIComponent(key))
      recovered = receipt.run
    } catch { /* 保留结果未知的恢复说明，不能把网络故障当作拒绝。 */ }
    if (recovered) return checked(recovered)
    throw new ResourceError({key: 'ui.lib.runHistory.ee75b48acc01'})
  }
}
