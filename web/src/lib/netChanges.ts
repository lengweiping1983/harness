import { t } from '@/i18n'
import type { NetChangedFile, NetFilePatch, TurnNetChanges } from './protocol.generated'
import type { ChangesTarget } from './turnChanges'
import { runtimeRequest, ApiError } from './http'

const invalid = () => new ApiError({ok: false, message: t("ui.lib.netChanges.502f59eea0d1"), copy: {key: "ui.lib.netChanges.502f59eea0d1"}, code: 'changes/invalid-target', status: 409})
const integer = (value: number) => Number.isSafeInteger(value) && value >= 0
const key = (path: string) => /^[A-Za-z]:/.test(path) ? path.replaceAll('\\', '/').toLowerCase() : path
function prefix(target: ChangesTarget) {
  if (!target.sessionId || !integer(target.turn) || target.turn === 0) throw invalid()
  return '/sessions/' + encodeURIComponent(target.sessionId) + '/turns/' + target.turn + '/net-changes'
}
function validFile(file: NetChangedFile, through: number) {
  return !!file && typeof file.path === 'string' && !!file.path.trim() && integer(file.first_seq) && integer(file.last_seq)
    && file.first_seq <= file.last_seq && file.last_seq <= through && integer(file.operations) && file.operations > 0
    && typeof file.available === 'boolean' && typeof file.truncated === 'boolean'
    && ['unknown', 'unchanged', 'created', 'modified'].includes(file.kind)
    && (file.available ? file.kind !== 'unknown' && file.reason === null : file.kind === 'unknown' && typeof file.reason === 'string')
    && (file.counts === null || !!file.counts && integer(file.counts.added) && integer(file.counts.removed))
    && (!file.truncated || file.counts === null)
}
export async function readNetChanges(target: ChangesTarget, signal?: AbortSignal, page?: { offset: number; through: number }): Promise<TurnNetChanges> {
  const query = page ? `?offset=${page.offset}&through=${page.through}` : ''
  const value = await runtimeRequest<TurnNetChanges>(prefix(target) + query, 'GET', undefined, { signal })
  if (!value || value.session_id !== target.sessionId || value.turn !== target.turn || !integer(value.through)
    || !Array.isArray(value.files) || value.files.some(file => !validFile(file, value.through))
    || new Set(value.files.map(file => key(file.path))).size !== value.files.length
    || !integer(value.total_files) || value.total_files < value.files.length || !integer(value.uncertain_operations)
    || typeof value.ended !== 'boolean' || value.offset !== (page?.offset ?? 0) || page && value.through !== page.through
    || value.next_offset !== null && (!integer(value.next_offset) || value.next_offset <= value.offset)) throw invalid()
  return value
}
export function appendNetChanges(previous: TurnNetChanges, next: TurnNetChanges): TurnNetChanges {
  if (previous.session_id !== next.session_id || previous.turn !== next.turn || previous.through !== next.through
    || previous.next_offset !== next.offset || previous.total_files !== next.total_files
    || next.files.some(file => previous.files.some(item => key(item.path) === key(file.path)))) throw invalid()
  return { ...previous, files: [...previous.files, ...next.files], next_offset: next.next_offset }
}
export async function readNetFile(target: ChangesTarget, file: NetChangedFile, through: number, signal?: AbortSignal): Promise<NetFilePatch> {
  if (!integer(through) || !validFile(file, through)) throw invalid()
  const value = await runtimeRequest<NetFilePatch>(prefix(target) + '/' + file.first_seq + '?through=' + through, 'GET', undefined, { signal })
  if (!value || value.session_id !== target.sessionId || value.turn !== target.turn || value.through !== through
    || !validFile(value, through) || value.path !== file.path || value.first_seq !== file.first_seq || value.last_seq !== file.last_seq
    || value.operations !== file.operations || value.available !== file.available || value.kind !== file.kind
    || (value.available ? typeof value.patch !== 'string' : value.patch !== null)) throw invalid()
  return value
}
