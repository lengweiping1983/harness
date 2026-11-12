import { ResourceError } from './resourceError'
import { t } from '@/i18n'
import { runtimeRequest, ApiError } from './http'
import type { QueueEntry as QueuedInput, SteerAccepted, SteeringReceipt, SteeringStatus } from './protocol.generated'

export interface QueueEntry extends QueuedInput {
  goal_id?: string | null
  kind: 'runs'
}

export class QueueSaveUnknown extends ResourceError {
  constructor() { super({key: 'ui.lib.queueEdits.ca71bc9168b4'}) }
}

export function competingQueueEntry(error: unknown, entry: QueueEntry): QueueEntry | null {
  if (!(error instanceof ApiError) || error.code !== 'run/queue-changed') return null
  const current = (error.details as { current?: Partial<QueueEntry> } | undefined)?.current
  if (!current || current.id !== entry.id || typeof current.text !== 'string' ||
      typeof current.edit_version !== 'string' || !/^[a-f0-9]{64}$/.test(current.edit_version)) return null
  return { ...entry, text: current.text, edit_version: current.edit_version }
}

function path(sessionId: string, entry: QueueEntry): string {
  return '/sessions/' + encodeURIComponent(sessionId) + '/' + entry.kind + '/' + encodeURIComponent(entry.id)
}

/** Only inspect after a lost acknowledgement; never replay PATCH. */
export async function verifyQueuedInput(sessionId: string, entry: QueueEntry, text: string): Promise<void> {
  try {
    const current = await runtimeRequest<{ id: string; session_id: string; text: string }>(path(sessionId, entry))
    if (current.id === entry.id && current.session_id === sessionId && current.text === text) return
  } catch { /* A failed read cannot establish whether the earlier write committed. */ }
  throw new QueueSaveUnknown()
}

export async function saveQueuedInput(sessionId: string, entry: QueueEntry, text: string): Promise<void> {
  try {
    await runtimeRequest(path(sessionId, entry), 'PATCH', { text, expected_version: entry.edit_version })
  } catch (error) {
    if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) throw error
    await verifyQueuedInput(sessionId, entry, text)
  }
}

export async function removeQueuedInput(sessionId: string, entry: QueueEntry): Promise<void> {
  await runtimeRequest(path(sessionId, entry) + '?expected_version=' + encodeURIComponent(entry.edit_version), 'DELETE')
}

export class QueueWithdrawalUnknown extends ResourceError {
  constructor() { super({key: 'ui.lib.queueEdits.withdrawUnknown'}) }
}

/** Verification is read-only, including after a refresh. */
export async function verifyWithdrawnInput(sessionId: string, entry: QueueEntry): Promise<void> {
  let current: { id: string; session_id: string; text: string; status?: string; edit_version?: string; error?: { code: string } }
  try { current = await runtimeRequest(path(sessionId, entry)) }
  catch { throw new QueueWithdrawalUnknown() }
  if (current.id === entry.id && current.session_id === sessionId) {
    if (current.text === entry.text && current.error?.code === 'run/removed') return
    if (current.status === 'queued') {
      if (current.text !== entry.text) throw new ApiError({ok: false, message: t("ui.lib.queueEdits.withdrawChanged"), copy: {key: "ui.lib.queueEdits.withdrawChanged"}, code: 'run/queue-changed', status: 409, details: { current }})
      throw new QueueWithdrawalUnknown()
    }
    if (current.status) throw new ApiError({ok: false, message: t("ui.lib.queueEdits.withdrawStarted"), copy: {key: "ui.lib.queueEdits.withdrawStarted"}, code: 'run/not-queued', status: 409})
  }
  throw new QueueWithdrawalUnknown()
}

/** Restore only after removal is confirmed; never retry an ambiguous DELETE. */
export async function withdrawQueuedInput(sessionId: string, entry: QueueEntry): Promise<void> {
  try { await removeQueuedInput(sessionId, entry) }
  catch (failure) {
    if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && failure.status !== 408) throw failure
    await verifyWithdrawnInput(sessionId, entry)
  }
}

export async function readQueuedInputs(sessionId: string, signal?: AbortSignal): Promise<QueueEntry[]> {
  const { runs } = await runtimeRequest<{ runs: QueuedInput[] }>('/sessions/' + encodeURIComponent(sessionId) + '/queue', 'GET', undefined, { signal })
  return runs.map(entry => ({ ...entry, kind: 'runs' as const }))
}

/** The server transfers the original input; attachments stay in its full text. */
export async function steerQueuedInput(sessionId: string, entry: QueueEntry, turn: number): Promise<SteeringReceipt> {
  try {
    const result = await runtimeRequest<SteerAccepted>(path(sessionId, entry) + '/steer', 'POST', {
      expected_turn: turn, expected_version: entry.edit_version,
    })
    return result.receipt
  } catch (failure) {
    if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && failure.status !== 408) throw failure
    // A lost response is checked by the stable receipt identity, never reposted.
    try {
      const result = await runtimeRequest<SteeringStatus>('/sessions/' + encodeURIComponent(sessionId) + '/steering/' + encodeURIComponent('queue-steer:' + entry.id))
      const entries = await readQueuedInputs(sessionId)
      if (result.receipt && !entries.some((item) => item.id === entry.id)) return result.receipt
    } catch { /* Keep the row until the original transfer can be verified. */ }
    throw new ResourceError({key: 'ui.lib.queueEdits.25bb4127c0b6'})
  }
}

/** 排序有完整快照比较；响应丢失只回查，不能重复发送相对移动。 */
export async function moveQueuedInput(sessionId: string, entries: QueueEntry[], entry: QueueEntry, direction: -1 | 1): Promise<QueueEntry[]> {
  const index = entries.findIndex(row => row.id === entry.id)
  const target = index + direction
  if (index < 0 || target < 0 || target >= entries.length) return entries
  const ordered = entries.slice()
  ;[ordered[index], ordered[target]] = [ordered[target]!, ordered[index]!]
  const order = ordered.map(row => row.id)
  try {
    await runtimeRequest('/sessions/' + encodeURIComponent(sessionId) + '/queue-order', 'POST', {
      kind: entry.kind, expected: entries.map(row => ({id: row.id, edit_version: row.edit_version})), order,
    })
  } catch (failure) {
    if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && failure.status !== 408) throw failure
    const fresh = await readQueuedInputs(sessionId)
    if (JSON.stringify(fresh.map(row => row.id)) === JSON.stringify(order)) return fresh
    throw new ResourceError({key: 'ui.queue.orderChanged'})
  }
  return ordered
}
