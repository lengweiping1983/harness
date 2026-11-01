import { t } from '@/i18n'
import type { RunRecord } from './protocol.generated'
import { runtimeRequest, ApiError } from './http'

export type RunRevealTarget = { sessionId: string; turn?: number; runId?: string; seq: number }

/** Read a precise same-chat identity, without submitting or scanning history pages. */
export async function readRunTarget(target: RunRevealTarget, signal?: AbortSignal): Promise<RunRecord> {
  const prefix = '/sessions/' + encodeURIComponent(target.sessionId)
  const path = target.runId ? prefix + '/runs/' + encodeURIComponent(target.runId)
    : Number.isSafeInteger(target.turn) && target.turn! > 0 ? prefix + '/turns/' + target.turn + '/run' : null
  if (!path || !target.sessionId) throw new ApiError({ok: false, message: t("ui.lib.runReveal.3b7cb0f78970"), copy: {key: 'ui.lib.runReveal.3b7cb0f78970'}, code: 'run/invalid-target', status: 400})
  const record = await runtimeRequest<RunRecord>(path, 'GET', undefined, { signal })
  if (!record || typeof record.id !== 'string' || !record.id || record.session_id !== target.sessionId || target.runId && record.id !== target.runId) {
    throw new ApiError({ok: false, message: t("ui.lib.runReveal.1b916fec6f48"), copy: {key: 'ui.lib.runReveal.1b916fec6f48'}, code: 'run/invalid-target', status: 409})
  }
  return record
}
