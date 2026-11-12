import {create} from 'zustand'

type Review = {id: string; sessionId: string | null; path: string}
export const usePluginReview = create<{
  request: Review | null
  open: (sessionId: string | null, path: string) => void
  consume: (id: string) => void
}>(set => ({
  request: null,
  open: (sessionId, path) => set({request: {id: crypto.randomUUID(), sessionId, path}}),
  consume: id => set(state => state.request?.id === id ? {request: null} : state),
}))
