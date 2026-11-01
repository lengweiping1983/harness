import {afterEach, expect, it, vi} from 'vitest'
import {useAppStore} from '../app'
import {api} from '@/lib/api'
import {getFileRoot, setFileRoot} from '@/lib/fileRoot'
import type {SessionSummary} from '@/types'

afterEach(() => vi.restoreAllMocks())
it('deletion follows the current conversation directory, including a delayed reply and the final conversation', async () => {
  for (const delayed of [false, true]) {
    const sessions = [{id: 'a', cwd: '/fixture/a'}, {id: 'b', cwd: '/fixture/b', resolved_cwd: '/resolved/b'}] as SessionSummary[]
    useAppStore.setState({sessions, activeSessionId: 'a', activeWorkspaceId: null, workspaces: [], pendingQuote: {seq: 1, sessionId: 'a', text: 'original'}})
    setFileRoot('/fixture/a')
    let finish!: (value: Awaited<ReturnType<typeof api.deleteSession>>) => void
    vi.spyOn(api, 'deleteSession').mockImplementation(() => new Promise(resolve => {finish = resolve}))
    const removal = useAppStore.getState().removeSession('a')
    await vi.waitFor(() => expect(api.deleteSession).toHaveBeenCalledWith('a'))
    if (delayed) {
      useAppStore.getState().selectSession('b')
      useAppStore.setState({pendingQuote: {seq: 2, sessionId: 'b', text: 'new quote'}})
    }
    finish({ok: true, data: {deleted: 'a'}})
    await removal
    expect(useAppStore.getState().activeSessionId).toBe('b')
    expect(getFileRoot()).toBe('/resolved/b')
    expect(useAppStore.getState().pendingQuote?.text ?? null).toBe(delayed ? 'new quote' : null)
    vi.mocked(api.deleteSession).mockResolvedValue({ok: true, data: {deleted: 'b'}})
    await useAppStore.getState().removeSession('b')
    expect(useAppStore.getState().activeSessionId).toBeNull()
    expect(getFileRoot()).toBeNull()
    vi.restoreAllMocks()
  }
})
