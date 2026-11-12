import type {ResourceText} from '@/types'
import {failureText, ResourceError} from '@/lib/resourceError'
import { create } from 'zustand'
import { runtimeRequest, ApiError } from '@/lib/http'

export interface StopRequest {
  turn: number
  phase: 'pending' | 'accepted' | 'rejected' | 'unconfirmed'
  message?: ResourceText
}
interface StopStore {
  requests: Record<string, StopRequest | undefined>
  observe: (sessionId: string, running: boolean, turn: number) => void
  stop: (sessionId: string, turn: number, refresh: () => void) => Promise<void>
}
/** Window-owned intent; completion is owned by the server activity stream. */
export const useStopStore = create<StopStore>((set, get) => ({
  requests: {},
  observe: (sessionId, running, turn) => {
    const request = get().requests[sessionId]
    if (request && (!running || request.turn !== turn)) set((state) => {
      const requests = { ...state.requests }
      delete requests[sessionId]
      return { requests }
    })
  },
  stop: async (sessionId, turn, refresh) => {
    if (!Number.isInteger(turn) || turn < 1) { refresh(); return }
    if (['pending', 'accepted'].includes(get().requests[sessionId]?.phase ?? '')) return
    const request: StopRequest = { turn, phase: 'pending' }
    set((state) => ({ requests: { ...state.requests, [sessionId]: request } }))
    const settle = (feedback: StopRequest) => {
      if (get().requests[sessionId] === request) set((state) => ({ requests: { ...state.requests, [sessionId]: feedback } }))
    }
    try {
      const result = await runtimeRequest<{ requested: boolean }>('/sessions/' + encodeURIComponent(sessionId) + '/stop', 'POST',
        { expected_turn: turn }, { signal: AbortSignal.timeout(10000) })
      if (typeof result.requested !== 'boolean') throw new ResourceError({key: "ui.store.stops.368d196fe987"})
      settle({ turn, phase: result.requested ? 'accepted' : 'unconfirmed',
        ...(result.requested ? {} : { message: {key: "ui.store.stops.5ad9d939a628"} }) })
    } catch (error) {
      const rejected = error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408
      settle({ turn, phase: rejected ? 'rejected' : 'unconfirmed', message: failureText(error) })
    } finally { refresh() }
  },
}))
