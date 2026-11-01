import { t } from '@/i18n'
import type { TurnChanges } from './protocol.generated'
import { runtimeRequest, ApiError } from './http'

export type ChangesTarget = { sessionId: string; turn: number; seq: number; path?: string }
const invalid = () => new ApiError({ok: false, message: t("ui.lib.turnChanges.1ed5550aaff5"), copy: {key: "ui.lib.turnChanges.1ed5550aaff5"}, code: 'changes/invalid-target', status: 409})
function prefix(target: ChangesTarget) {
  if (!target.sessionId || !Number.isSafeInteger(target.turn) || target.turn <= 0) throw invalid()
  return '/sessions/' + encodeURIComponent(target.sessionId) + '/turns/' + target.turn + '/changes'
}
const fileKey = (path: string) => /^[A-Za-z]:/.test(path) ? path.replaceAll('\\', '/').toLowerCase() : path
export async function readTurnChanges(target: ChangesTarget, signal?: AbortSignal, page?: { offset: number; through: number }): Promise<TurnChanges> {
  const query = page ? `?offset=${page.offset}&through=${page.through}` : ''
  const value = await runtimeRequest<TurnChanges>(prefix(target) + query, 'GET', undefined, { signal })
  if (!value || value.session_id !== target.sessionId || value.turn !== target.turn || !Array.isArray(value.files)
      || value.files.some(file => typeof file.path !== 'string' || !Array.isArray(file.operations)
        || file.operations.some(operation => typeof operation.path !== 'string' || fileKey(operation.path) !== fileKey(file.path) || !Number.isSafeInteger(operation.source_seq) || operation.source_seq < 0))
      || !Number.isSafeInteger(value.through) || value.offset !== (page?.offset ?? 0) || page && value.through !== page.through
      || value.next_offset !== null && (!Number.isSafeInteger(value.next_offset) || value.next_offset <= value.offset)) throw invalid()
  return value
}
