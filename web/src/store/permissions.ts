import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { create } from 'zustand'
import { runtimeRequest, ApiError } from '@/lib/http'
import type { PermissionProfile } from '@/lib/permissions'
import type { SessionPermissionState } from '@/lib/protocol.generated'

type Settings = { config: { permission_policy: string } }
type SessionPermissions = { permissions: SessionPermissionState }
let revision = 0
let mutation = 0
export const usePermissionStore = create<{
  policy: string | null
  sessionId: string | null
  scope: string | null
  inherited: boolean
  version: string | null
  loading: boolean
  busy: boolean
  error: ResourceText | null
  readError: ResourceText | null
  saveError: ResourceText | null
  saveUnconfirmed: boolean
  dismissSaveError: () => void
  receive: (state: SessionPermissionState) => void
  load: (sessionId?: string | null) => Promise<void>
  save: (profile: PermissionProfile | null) => Promise<boolean>
}>((set, get) => ({
  policy: null, sessionId: null, scope: null, inherited: true, version: null, loading: false, busy: false, error: null,
  readError: null, saveError: null, saveUnconfirmed: false,
  dismissSaveError: () => set({ saveError: null, saveUnconfirmed: false, error: get().readError }),
  receive: (state) => {
    if (!state || state.session_id !== get().sessionId || typeof state.version !== 'string' || state.version.length !== 64) return
    ++revision
    set({ policy: state.permission_policy, scope: state.scope, version: state.version, inherited: state.inherited, loading: false, readError: null, error: get().saveError })
  },
  load: async (sessionId = null) => {
    if (get().busy && get().sessionId === sessionId) return
    const current = ++revision
    const switched = get().sessionId !== sessionId
    if (switched) ++mutation
    set({ sessionId, loading: true, readError: null, error: switched ? null : get().saveError,
      ...(switched ? { policy: null, scope: null, version: null, busy: false, saveError: null, saveUnconfirmed: false } : {}) })
    try {
      if (sessionId) {
        const { permissions } = await runtimeRequest<SessionPermissions>(`/sessions/${encodeURIComponent(sessionId)}/permissions`)
        if (current === revision) set({ policy: permissions.permission_policy, scope: permissions.scope ?? sessionId, version: permissions.version, inherited: permissions.inherited })
      } else {
        const data = await runtimeRequest<Settings>('/settings')
        if (current === revision) set({ policy: data.config.permission_policy, version: null, inherited: true })
      }
    } catch (error) { if (current === revision) set({ readError: failureText(error), error: get().saveError || failureText(error) }) }
    finally { if (current === revision) set({ loading: false }) }
  },
  save: async (profile) => {
    if (get().busy || get().loading) return false
    const sessionId = get().sessionId
    if (sessionId && get().scope && get().scope !== sessionId) {
      set({ saveError: {key: "ui.store.permissions.1debdc254a70"}, saveUnconfirmed: false, error: {key: "ui.store.permissions.1debdc254a70"} })
      return false
    }
    const version = get().version
    if (sessionId && !version || !sessionId && profile === null) return false
    const current = ++revision
    const ticket = ++mutation
    set({ busy: true, loading: false, saveError: null, saveUnconfirmed: false, error: get().readError })
    try {
      if (sessionId) {
        const { permissions } = await runtimeRequest<SessionPermissions>(`/sessions/${encodeURIComponent(sessionId)}/permissions`, 'PATCH', {
          permission_policy: profile, expected_version: version,
        })
        if (ticket !== mutation || get().sessionId !== sessionId) return false
        if (current === revision) set({ policy: permissions.permission_policy, version: permissions.version, inherited: permissions.inherited })
        else if (get().version !== permissions.version) {
          set({ saveError: {key: "ui.store.permissions.933df1eb9298"}, saveUnconfirmed: false, error: {key: "ui.store.permissions.933df1eb9298"} })
          return false
        }
      } else {
        const data = await runtimeRequest<Settings>('/settings', 'PATCH', { permission_policy: profile })
        if (current === revision) set({ policy: data.config.permission_policy })
      }
      return ticket === mutation && get().sessionId === sessionId
    } catch (error) {
      if (ticket === mutation && get().sessionId === sessionId) {
        if (error instanceof ApiError && error.code === 'permissions/changed') {
          const state = error.details as SessionPermissions['permissions'] | undefined
          if (current === revision && state?.version) set({ policy: state.permission_policy, version: state.version, inherited: state.inherited })
        }
        set({ saveError: failureText(error), error: failureText(error),
          saveUnconfirmed: !(error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) })
      }
      return false
    } finally { if (ticket === mutation && get().sessionId === sessionId) set({ busy: false }) }
  },
}))
