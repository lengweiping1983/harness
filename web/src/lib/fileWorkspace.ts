import { api, unwrap } from '@/lib/api'
import { normalizePath } from '@/lib/workspaces'
import { useAppStore } from '@/store/app'

const pending = new Map<string, Promise<void>>()

/** 登记可信的对话目录，保留调用期间用户选择的对话和项目。 */
export function ensureFileWorkspace(root: string): Promise<void> {
  const key = normalizePath(root)
  const state = useAppStore.getState()
  if (normalizePath(state.workspace?.root) === key || state.workspaces.some(item => normalizePath(item.path) === key)) return Promise.resolve()
  const existing = pending.get(key)
  if (existing) return existing
  const request = unwrap(api.registerWorkspace({path: root})).then(({workspace}) => {
    useAppStore.setState(current => ({workspaces: current.workspaces.some(item => item.id === workspace.id)
      ? current.workspaces : [...current.workspaces, workspace]}))
  }).finally(() => { if (pending.get(key) === request) pending.delete(key) })
  pending.set(key, request)
  return request
}
