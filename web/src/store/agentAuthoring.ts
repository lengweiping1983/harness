import type {ResourceText} from '@/types'
import {create} from 'zustand'
import type {AuthoringSubmission} from '@/lib/agentAuthoring'
import type {RunRecord} from '@/lib/protocol.generated'

export type AuthoringDraft = {requirements: string; pending?: boolean; submission?: AuthoringSubmission; run?: RunRecord; error?: ResourceText}
type State = {drafts: Record<string, AuthoringDraft>; update: (key: string, patch: Partial<AuthoringDraft>) => void; discard: (key: string) => void}
export const useAgentAuthoring = create<State>(set => ({
  drafts: {},
  update: (key, patch) => set(state => ({drafts: {...state.drafts, [key]: {...state.drafts[key], requirements: state.drafts[key]?.requirements ?? '', ...patch}}})),
  discard: key => set(state => {const drafts = {...state.drafts}; delete drafts[key]; return {drafts}}),
}))
let guarded = false
const beforeUnload = (event: BeforeUnloadEvent) => {
  if (Object.values(useAgentAuthoring.getState().drafts).some(draft => draft.requirements.trim() && !draft.run || draft.submission && !draft.run)) {
    event.preventDefault(); event.returnValue = ''
  }
}
export function guardAgentAuthoring() {
  if (guarded || typeof window === 'undefined') return
  guarded = true
  window.addEventListener('beforeunload', beforeUnload)
}
if (import.meta.hot) import.meta.hot.dispose(() => {
  if (typeof window !== 'undefined') window.removeEventListener('beforeunload', beforeUnload)
  guarded = false
})