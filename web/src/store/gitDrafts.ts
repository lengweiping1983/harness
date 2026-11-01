import { create } from 'zustand'
import { normalizePath } from '@/lib/workspaces'
import { createWindowDraftStorage } from '@/lib/windowDraftStorage'

const STORAGE_KEY = 'harness.git-drafts.v1'
export function gitDraftScope(sessionId: string, root: string): string {
  return JSON.stringify([sessionId, normalizePath(root)])
}
export function gitDraftBelongsToSession(scope: string, sessionId: string): boolean {
  return scope.startsWith(JSON.stringify([sessionId]).slice(0, -1) + ',')
}

type GitDraftState = {
  byScope: Record<string, string>
  revisionByScope: Record<string, number>
  storageError: boolean
  storageCorrupt: boolean
  setMessage: (scope: string, message: string) => void
  retryStorage: () => void
  discardSession: (sessionId: string) => void
}

/** Drafts belong to this browser window, so another window cannot erase edits. */
export function createGitDraftStore(storage = createWindowDraftStorage()) {
  let initial: Record<string, string> = {}
  let storageCorrupt = false
  try {
    const value: unknown = JSON.parse(storage.read(STORAGE_KEY) ?? '{}')
    if (!value || typeof value !== 'object' || Array.isArray(value) ||
        Object.values(value).some(message => typeof message !== 'string' || message.length > 8000)) {
      storageCorrupt = true
    } else initial = value as Record<string, string>
  } catch { storageCorrupt = true }
  // 损坏原件不能被当前页面的编辑或重试覆盖；新内容仍保留在内存。
  const save = (drafts: Record<string, string>) => !storageCorrupt && storage.write(STORAGE_KEY, Object.keys(drafts).length ? JSON.stringify(drafts) : null)
  let revision = 0
  return create<GitDraftState>((set, get) => ({
    byScope: initial,
    revisionByScope: {},
    storageError: storageCorrupt,
    storageCorrupt,
    setMessage: (scope, message) => set(state => {
      const byScope = { ...state.byScope }
      if (message) byScope[scope] = message
      else delete byScope[scope]
      return { byScope, revisionByScope: {...state.revisionByScope, [scope]: ++revision}, storageError: !save(byScope) }
    }),
    retryStorage: () => set({ storageError: !save(get().byScope) }),
    discardSession: sessionId => set(state => {
      const entries = Object.entries(state.byScope).filter(([scope]) => !gitDraftBelongsToSession(scope, sessionId))
      if (entries.length === Object.keys(state.byScope).length) return state
      const byScope = Object.fromEntries(entries)
      const revisionByScope = Object.fromEntries(Object.entries(state.revisionByScope).filter(([scope]) => !gitDraftBelongsToSession(scope, sessionId)))
      return { byScope, revisionByScope, storageError: !save(byScope) }
    }),
  }))
}

export const useGitDraftStore = createGitDraftStore()
