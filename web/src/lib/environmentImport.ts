import { ResourceError } from '@/lib/resourceError'
import { runtimeRequest, ApiError } from '@/lib/http'

export type EnvironmentReceipt = {
  receipt_id: string; worktree_id: string; fingerprint: string
  status: 'preparing' | 'ready' | 'failed'; planned_count: number
  copied_count: number; skipped_count: number; reused: boolean; truncated: boolean
  entries: Array<{path: string; action: 'copied' | 'skip'; reason?: string}>
}

export async function importEnvironment(session: string, worktree: string, fingerprint: string): Promise<EnvironmentReceipt> {
  if (!/^[a-f0-9]{64}$/.test(fingerprint) || readEnvironmentPending(session, worktree)) throw new ResourceError({key: 'ui.environment.selectionRequired'})
  saveEnvironmentPending(session, worktree, fingerprint)
  try {
    const result = await runtimeRequest<{receipt: EnvironmentReceipt}>(`/sessions/${encodeURIComponent(session)}/worktrees/${encodeURIComponent(worktree)}/environment`, 'POST', {expected_fingerprint: fingerprint})
    if (!environmentReceiptConfirms(fingerprint, result.receipt, worktree)) throw new ResourceError({key: 'ui.environment.unconfirmed'})
    saveEnvironmentPending(session, worktree, null)
    if (result.receipt.status !== 'ready') throw new ResourceError({key: 'ui.environment.failedReceipt'})
    return result.receipt
  } catch (failure) {
    if (failure instanceof ApiError && environmentRejectedBeforeWrite(failure.code)) saveEnvironmentPending(session, worktree, null)
    throw failure
  }
}

export async function prepareCreatedWorktree(session: string, worktree: string): Promise<EnvironmentReceipt | null> {
  const preview = await runtimeRequest<{configured: boolean; copy_count: number; fingerprint: string}>(`/sessions/${encodeURIComponent(session)}/worktrees/${encodeURIComponent(worktree)}/environment`)
  if (preview.configured === false || (preview.configured === true && preview.copy_count === 0)) return null
  if (preview.configured !== true || !Number.isSafeInteger(preview.copy_count) || preview.copy_count < 0) throw new ResourceError({key: 'ui.environment.invalidSelection'})
  return importEnvironment(session, worktree, preview.fingerprint)
}

const key = (session: string, worktree: string) => 'harness.environment.pending.' + encodeURIComponent(session) + '.' + encodeURIComponent(worktree)
export function readEnvironmentPending(session: string, worktree: string): string | null {
  try {
    const value = localStorage.getItem(key(session, worktree))
    return value && /^[a-f0-9]{64}$/.test(value) ? value : null
  } catch { return null }
}
export function saveEnvironmentPending(session: string, worktree: string, fingerprint: string | null): void {
  try {
    if (fingerprint === null) localStorage.removeItem(key(session, worktree))
    else localStorage.setItem(key(session, worktree), fingerprint)
  } catch { /* 浏览器存储不可用时仍保留当前界面的待核实状态。 */ }
}
export function environmentReceiptConfirms(pending: string | null, receipt: EnvironmentReceipt | null, worktree: string): boolean {
  return Boolean(pending && receipt?.worktree_id === worktree && receipt.fingerprint === pending && (receipt.status === 'ready' || receipt.status === 'failed'))
}
export function environmentRejectedBeforeWrite(code: string): boolean {
  return ['git/environment-read-only', 'git/file-changed', 'git/archived', 'git/not-found', 'git/running', 'agent/run-active', 'git/environment-invalid', 'git/disabled', 'git/invalid'].includes(code)
}
