import {beforeEach, describe, expect, it, vi} from 'vitest'
vi.mock('@/lib/http', async importOriginal => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {...actual, runtimeRequest: vi.fn()}
})
import {runtimeRequest} from '@/lib/http'
import {useStopStore} from '../stops'
const send = vi.mocked(runtimeRequest)

beforeEach(() => {
  send.mockReset()
  useStopStore.setState({requests: {}})
})

describe('停止意图与服务端活动事实', () => {
  it('停止被接受不等于已结束，由活动观察清除意图', async () => {
    send.mockResolvedValueOnce({requested: true})
    const refresh = vi.fn()
    await useStopStore.getState().stop('first', 1, refresh)
    expect(useStopStore.getState().requests.first?.phase).toBe('accepted')
    useStopStore.getState().observe('first', true, 1)
    expect(useStopStore.getState().requests.first?.phase).toBe('accepted')
    useStopStore.getState().observe('first', false, 1)
    expect(useStopStore.getState().requests.first).toBeUndefined()
    expect(refresh).toHaveBeenCalledOnce()
  })

  it('回合已结束时，较晚回执不恢复旧停止意图', async () => {
    let resolve!: (value: {requested: boolean}) => void
    send.mockReturnValueOnce(new Promise(done => {resolve = done}))
    const stop = useStopStore.getState().stop('first', 1, vi.fn())
    useStopStore.getState().observe('first', true, 2)
    resolve({requested: true})
    await stop
    expect(useStopStore.getState().requests.first).toBeUndefined()
  })

  it('停止断线只标记未知，不重发，也不影响另一会话', async () => {
    useStopStore.setState({requests: {other: {turn: 3, phase: 'accepted'}}})
    send.mockRejectedValueOnce(new Error('连接中断'))
    await useStopStore.getState().stop('first', 1, vi.fn())
    expect(send).toHaveBeenCalledTimes(1)
    expect(useStopStore.getState().requests.first?.phase).toBe('unconfirmed')
    expect(useStopStore.getState().requests.other).toEqual({turn: 3, phase: 'accepted'})
  })
})
