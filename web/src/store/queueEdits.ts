import type { ResourceCopy } from '@/types'
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import {validPersistedQueueEdits} from '@/lib/draftPersistence'
import { createBrowserDraftStorage } from '@/lib/browserDraftStorage'
import type { QueueEntry } from '@/lib/queueEdits'

export interface QueueEditDraft {
  entry: QueueEntry
  draft: string
  unconfirmed?: { entry: QueueEntry; text: string }
}

interface QueueEditState {
  bySession: Record<string, QueueEditDraft>
  storageError: ResourceCopy | null
  withdrawals: Record<string, QueueEntry>
  setWithdrawal: (sessionId: string, entry: QueueEntry | null) => void
  setEdit: (sessionId: string, draft: QueueEditDraft | null) => void
}

// Each browser tab owns its editor. Sharing localStorage would let a second
// tab erase the first tab's conflicting draft immediately before a refresh.
let reported: 'invalid' | 'unavailable' | null = null
function failed(reason?: 'invalid') {
  const kind = reason ?? 'unavailable'
  if (reported === kind || reported === 'invalid') return
  reported = kind
  queueMicrotask(() => useQueueEditStore.setState({ storageError: {key: reason === 'invalid' ? 'ui.queueDrafts.invalidStorage' : 'ui.store.queueEdits.821e01199e43'} }))
}
const storage = createJSONStorage(() => createBrowserDraftStorage(
  () => typeof sessionStorage === 'undefined' ? undefined : sessionStorage, failed, validPersistedQueueEdits,
))

export const useQueueEditStore = create<QueueEditState>()(persist((set) => ({
  bySession: {},
  withdrawals: {},
  setWithdrawal: (sessionId, entry) => set(state => {
    const withdrawals = { ...state.withdrawals }
    if (entry) withdrawals[sessionId] = entry
    else delete withdrawals[sessionId]
    return { withdrawals }
  }),
  storageError: null,
  setEdit: (sessionId, draft) => set((state) => {
    const bySession = { ...state.bySession }
    if (draft) bySession[sessionId] = draft
    else delete bySession[sessionId]
    return { bySession }
  }),
}), { name: 'harness.queue-edits.v1', storage, partialize: (state) => ({ bySession: state.bySession, withdrawals: state.withdrawals }) }))
