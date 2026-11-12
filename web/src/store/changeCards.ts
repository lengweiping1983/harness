import { create } from 'zustand'
import type { ChangeRow } from '@/lib/fileChanges'
import { useAppStore } from './app'

type Card = Extract<ChangeRow, { kind: 'changes' }>
export const changeCardKey = (sessionId: string | null, row: Pick<Card, 'id' | 'pending'>) => JSON.stringify([sessionId, row.id, row.pending])
export const changeCardVisible = (row: Card, sessionId: string | null, choices: Record<string, boolean>) => choices[changeCardKey(sessionId, row)] ?? !row.pending

export const useChangeCards = create<{
  choices: Record<string, boolean>
  latest: Record<string, Card | undefined>
  target: { sessionId: string; id: string; seq: number } | null
  remember: (sessionId: string, row: Card | undefined) => void
  close: (sessionId: string | null, row: Pick<Card, 'id' | 'pending'>) => void
  showLatest: (sessionId: string | null) => boolean
}>((set, get) => ({
  choices: {}, latest: {}, target: null,
  remember: (sessionId, row) => set(state => ({ latest: { ...state.latest, [sessionId]: row } })),
  close: (sessionId, row) => set(state => ({ choices: { ...state.choices, [changeCardKey(sessionId, row)]: false } })),
  showLatest: sessionId => {
    const latest = get().latest
    const row = sessionId && Object.hasOwn(latest, sessionId) ? latest[sessionId] : undefined
    if (!sessionId || !row) return false
    set(state => ({ choices: { ...state.choices, [changeCardKey(sessionId, row)]: true }, target: { sessionId, id: row.id, seq: (state.target?.seq ?? 0) + 1 } }))
    return true
  },
}))

export function showLatestChanges() {
  if (!useChangeCards.getState().showLatest(useAppStore.getState().activeSessionId)) {
    useAppStore.getState().toast({ title: {key: "ui.store.changeCards.eb095692b3af"}, tone: 'info' })
  }
}
