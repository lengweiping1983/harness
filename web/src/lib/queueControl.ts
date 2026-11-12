import { ResourceError } from './resourceError'
import type { QueueControl } from './protocol.generated'
import { runtimeRequest, ApiError } from './http'

export class QueueControlUnknown extends ResourceError {
  constructor() { super({key: 'ui.lib.queueControl.96befea94f27'}) }
}

function validate(state: QueueControl, sessionId: string): QueueControl {
  if (!state || state.session_id !== sessionId || typeof state.paused !== 'boolean' || typeof state.running !== 'boolean' ||
      !Number.isInteger(state.pending) || state.pending < 0 || !['', 'user', 'stopped'].includes(state.pause_reason) ||
      (state.waiting_reason !== undefined && !['', 'environment'].includes(state.waiting_reason)) ||
      (state.updated_at !== null && (typeof state.updated_at !== 'number' || !Number.isFinite(state.updated_at)))) throw new ResourceError({key: 'ui.lib.queueControl.b46b7c15807a'})
  return state
}

export async function readQueueControl(sessionId: string): Promise<QueueControl> {
  return validate(await runtimeRequest<QueueControl>('/sessions/' + encodeURIComponent(sessionId) + '/queue-control'), sessionId)
}

export async function changeQueueControl(sessionId: string, paused: boolean): Promise<QueueControl> {
  try {
    const state = validate(await runtimeRequest<QueueControl>('/sessions/' + encodeURIComponent(sessionId) + '/queue-control', 'PATCH', { paused }), sessionId)
    if (state.paused === paused) return state
  } catch (error) {
    if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) throw error
  }
  try {
    const actual = await readQueueControl(sessionId)
    if (actual.paused === paused) return actual
  } catch { /* A failed verification is not proof of rejection. */ }
  throw new QueueControlUnknown()
}
