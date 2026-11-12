import type {ResourceText} from '@/types'
import {failureText} from '@/lib/resourceError'
import { create } from 'zustand'
import { api } from '@/lib/api'
import { readPlanModeState } from '@/lib/planModeState'
import { useChatStore } from './chat'

export interface PlanModeSave { target: boolean; pending: boolean; message?: ResourceText; unconfirmed?: boolean }
interface PlanModeStore {
  saves: Record<string, PlanModeSave | undefined>
  change: (sessionId: string, target: boolean) => Promise<void>
  dismiss: (sessionId: string) => void
}
export const usePlanModeStore = create<PlanModeStore>((set, get) => ({
  saves: {},
  dismiss: (sessionId) => {
    if (get().saves[sessionId]?.pending) return
    set((state) => { const saves = { ...state.saves }; delete saves[sessionId]; return { saves } })
  },
  change: async (sessionId, target) => {
    if (get().saves[sessionId]?.pending) return
    const initial = useChatStore.getState().get(sessionId).planModeRevision
    const save: PlanModeSave = { target, pending: true }
    set((state) => ({ saves: { ...state.saves, [sessionId]: save } }))
    let valid = true
    const owns = () => valid && get().saves[sessionId] === save
    const unsubscribe = useChatStore.subscribe((state, previous) => {
      if (previous.sessions[sessionId] && !state.sessions[sessionId]) {
        valid = false
        set(current => {
          if (current.saves[sessionId] !== save) return current
          const saves = { ...current.saves }
          delete saves[sessionId]
          return { saves }
        })
      }
    })
    const finish = (feedback?: PlanModeSave) => set((state) => {
      if (!owns()) return state
      const saves = { ...state.saves }
      if (feedback) saves[sessionId] = feedback
      else delete saves[sessionId]
      return { saves }
    })
    let message: ResourceText = {key: "ui.store.planModes.b82a0d9954e6"}
    try {
      const result = await api.setPlanMode(sessionId, target)
      if (!owns()) return
      const mode = result.ok ? readPlanModeState(result.data) : null
      const current = useChatStore.getState().get(sessionId)
      if (mode && current.planModeRevision === initial) useChatStore.getState().setPlanMode(sessionId, mode)
      if (mode && useChatStore.getState().get(sessionId).planMode === target) { finish(); return }
      if (!result.ok) message = result.copy ?? result.message
      const revision = useChatStore.getState().get(sessionId).planModeRevision
      const read = await api.planMode(sessionId)
      if (!owns()) return
      const actual = read.ok ? readPlanModeState(read.data.plan_mode) : null
      if (actual && useChatStore.getState().get(sessionId).planModeRevision === revision) useChatStore.getState().setPlanMode(sessionId, actual)
      if (actual && useChatStore.getState().get(sessionId).planMode === target) { finish(); return }
      finish({ target, pending: false, message, unconfirmed: !actual })
    } catch (error) {
      finish({ target, pending: false, message: failureText(error), unconfirmed: true })
    } finally {
      unsubscribe()
    }
  },
}))
