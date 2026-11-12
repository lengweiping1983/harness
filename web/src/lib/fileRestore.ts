import { t } from '@/i18n'
import type { FileRestorePreview, FileRestoreReceipt, NetChangedFile } from './protocol.generated'
import type { ChangesTarget } from './turnChanges'
import { runtimeRequest, ApiError } from './http'

const invalid = () => new ApiError({ok: false, message: t("ui.lib.fileRestore.84ec5178e924"), code: 'restore/invalid-target', status: 409, copy: {key: 'ui.lib.fileRestore.84ec5178e924'}})
function base(target: ChangesTarget, file: NetChangedFile) {
  if (!target.sessionId || !Number.isSafeInteger(target.turn) || target.turn <= 0 || !Number.isSafeInteger(file.first_seq) || file.first_seq < 0) throw invalid()
  return '/sessions/' + encodeURIComponent(target.sessionId) + '/turns/' + target.turn + '/net-changes/' + file.first_seq
}
function identity(value: { session_id: string; turn: number; source_seq: number; path: string }, target: ChangesTarget, file: NetChangedFile) {
  return !!value && value.session_id === target.sessionId && value.turn === target.turn && value.source_seq === file.first_seq && value.path === file.path
}
function validReceipt(value: FileRestoreReceipt, target: ChangesTarget, file: NetChangedFile, requestId?: string) {
  return identity(value, target, file) && typeof value.request_id === 'string' && /^[A-Za-z0-9_-]{8,128}$/.test(value.request_id)
    && (!requestId || value.request_id === requestId) && ['restored', 'rejected', 'unknown'].includes(value.status)
    && ['restore', 'remove'].includes(value.action) && typeof value.message === 'string' && Number.isFinite(value.time)
}
export async function readRestorePreview(target: ChangesTarget, file: NetChangedFile, through: number, signal?: AbortSignal) {
  if (!Number.isSafeInteger(through) || through < file.last_seq) throw invalid()
  const value = await runtimeRequest<FileRestorePreview>(base(target, file) + '/restore?through=' + through, 'GET', undefined, { signal })
  if (!identity(value, target, file) || value.through !== through || typeof value.available !== 'boolean' || typeof value.truncated !== 'boolean'
    || (value.available ? !['restore', 'remove'].includes(value.action) || value.reason !== null || typeof value.patch !== 'string' || !/^[0-9a-f]{64}$/.test(value.expected_version ?? '')
      : value.action !== 'none' || value.expected_version !== null || value.patch !== null || typeof value.reason !== 'string')
    || value.latest_receipt !== null && !validReceipt(value.latest_receipt, target, file)) throw invalid()
  return value
}
export async function readRestoreReceipt(target: ChangesTarget, file: NetChangedFile, requestId: string, signal?: AbortSignal) {
  if (!/^[A-Za-z0-9_-]{8,128}$/.test(requestId)) throw invalid()
  const value = await runtimeRequest<FileRestoreReceipt>(base(target, file) + '/restores/' + encodeURIComponent(requestId), 'GET', undefined, { signal })
  if (!validReceipt(value, target, file, requestId)) throw invalid()
  return value
}
export async function applyFileRestore(target: ChangesTarget, file: NetChangedFile, preview: FileRestorePreview, requestId: string) {
  if (!identity(preview, target, file) || !preview.available || !/^[0-9a-f]{64}$/.test(preview.expected_version ?? '') || !/^[A-Za-z0-9_-]{8,128}$/.test(requestId)) throw invalid()
  const value = await runtimeRequest<FileRestoreReceipt>(base(target, file) + '/restore', 'POST', {
    through: preview.through, expected_version: preview.expected_version, request_id: requestId,
  })
  if (!validReceipt(value, target, file, requestId) || value.action !== preview.action) throw invalid()
  return value
}
