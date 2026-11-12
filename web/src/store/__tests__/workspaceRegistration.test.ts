import {beforeEach, describe, expect, it, vi} from 'vitest'
import {api} from '@/lib/api'
import {useAppStore} from '../app'

beforeEach(() => {
  vi.restoreAllMocks()
  useAppStore.setState({activeSessionId: 'original', activeWorkspaceId: '/original', toasts: [], workspaces: []})
})

describe('项目注册不抢占当前界面选择', () => {
  it('注册成功更新目录并返回实际项目，不修改当前会话及项目', async () => {
    const record = {id: '/added', path: '/added', name: 'added', color: '#cccccc'}
    vi.spyOn(api, 'registerWorkspace').mockResolvedValue({ok: true, data: {workspace: record}})
    vi.spyOn(api, 'listWorkspaces').mockResolvedValue({ok: true, data: {workspaces: [record], default: '/default'}})
    await expect(useAppStore.getState().addWorkspace('/added')).resolves.toEqual(record)
    expect(useAppStore.getState().workspaces).toEqual([record])
    expect(useAppStore.getState().activeSessionId).toBe('original')
    expect(useAppStore.getState().activeWorkspaceId).toBe('/original')
  })

  it('注册拒绝不切换选择，不查询或自动重发', async () => {
    const register = vi.spyOn(api, 'registerWorkspace').mockResolvedValue({ok: false, status: 409, code: 'conflict', message: 'conflict'})
    const list = vi.spyOn(api, 'listWorkspaces')
    await expect(useAppStore.getState().addWorkspace('/added')).resolves.toBeNull()
    expect(register).toHaveBeenCalledTimes(1)
    expect(list).not.toHaveBeenCalled()
    expect(useAppStore.getState().activeWorkspaceId).toBe('/original')
  })
})
