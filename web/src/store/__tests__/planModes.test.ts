import {beforeEach, describe, expect, it, vi} from 'vitest'

vi.mock('@/lib/api', () => ({api: {setPlanMode: vi.fn(), planMode: vi.fn()}}))
import {api} from '@/lib/api'
import {useChatStore} from '../chat'
import {usePlanModeStore} from '../planModes'

const write = vi.mocked(api.setPlanMode)
const read = vi.mocked(api.planMode)
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => {resolve = done})
  return {promise, resolve}
}

beforeEach(() => {
  vi.resetAllMocks()
  useChatStore.setState({sessions: {}})
  usePlanModeStore.setState({saves: {}})
})

describe('计划模式的会话与确认状态边界', () => {
  it('等待写回执时保留确认状态，另一个会话不受影响', async () => {
    const pending = deferred<Awaited<ReturnType<typeof api.setPlanMode>>>()
    write.mockReturnValueOnce(pending.promise)
    useChatStore.getState().setPlanMode('other', {active: true, pending: null})
    const change = usePlanModeStore.getState().change('first', true)
    expect(useChatStore.getState().get('first').planMode).toBe(false)
    expect(usePlanModeStore.getState().saves.first?.pending).toBe(true)
    pending.resolve({ok: true, data: {active: true, pending: null}})
    await change
    expect(useChatStore.getState().get('first').planMode).toBe(true)
    expect(useChatStore.getState().get('other').planMode).toBe(true)
    expect(usePlanModeStore.getState().saves.first).toBeUndefined()
  })

  it('结果未知只回查同一会话，不重放写入', async () => {
    write.mockResolvedValueOnce({ok: false, status: 0, code: 'network', message: '回执丢失'})
    read.mockResolvedValueOnce({ok: true, data: {plan_mode: {active: true, pending: null}}})
    await usePlanModeStore.getState().change('first', true)
    expect(write).toHaveBeenCalledTimes(1)
    expect(read).toHaveBeenCalledExactlyOnceWith('first')
    expect(useChatStore.getState().get('first').planMode).toBe(true)
  })

  it('事件流已更新后，较晚写回执不能覆盖新状态', async () => {
    const pending = deferred<Awaited<ReturnType<typeof api.setPlanMode>>>()
    write.mockReturnValueOnce(pending.promise)
    read.mockResolvedValueOnce({ok: true, data: {plan_mode: {active: false, pending: null}}})
    const change = usePlanModeStore.getState().change('first', true)
    useChatStore.getState().setPlanMode('first', {active: false, pending: null})
    pending.resolve({ok: true, data: {active: true, pending: null}})
    await change
    expect(useChatStore.getState().get('first').planMode).toBe(false)
    expect(usePlanModeStore.getState().saves.first?.pending).toBe(false)
  })

  it('同一会话的重复点击不产生第二个写请求', async () => {
    const pending = deferred<Awaited<ReturnType<typeof api.setPlanMode>>>()
    write.mockReturnValueOnce(pending.promise)
    const change = usePlanModeStore.getState().change('first', true)
    await usePlanModeStore.getState().change('first', false)
    expect(write).toHaveBeenCalledTimes(1)
    pending.resolve({ok: true, data: {active: true, pending: null}})
    await change
  })
  it.each(['success', 'rejection', 'readback'] as const)('丢弃并重开同ID时旧%s不覆盖新状态或保存记录', async scenario => {
    const pending = deferred<Awaited<ReturnType<typeof api.setPlanMode>>>()
    const pendingRead = deferred<Awaited<ReturnType<typeof api.planMode>>>()
    useChatStore.getState().setPlanMode('first', {active: false, pending: null})
    if (scenario === 'readback') {
      write.mockResolvedValueOnce({ok:false, status:0, code:'network', message:'Original failure'})
      read.mockReturnValueOnce(pendingRead.promise)
    } else write.mockReturnValueOnce(pending.promise)
    const old = usePlanModeStore.getState().change('first', true)
    if (scenario === 'readback') await vi.waitFor(() => expect(read).toHaveBeenCalledTimes(1))
    useChatStore.getState().drop('first')
    useChatStore.getState().setPlanMode('first', {active: false, pending: null})
    const newer = deferred<Awaited<ReturnType<typeof api.setPlanMode>>>()
    write.mockReturnValueOnce(newer.promise)
    const current = usePlanModeStore.getState().change('first', false)
    const save = usePlanModeStore.getState().saves.first
    if (scenario === 'readback') pendingRead.resolve({ok:true, data:{plan_mode:{active:true,pending:null}}})
    else pending.resolve(scenario === 'success' ? {ok:true,data:{active:true,pending:null}} :
      {ok:false,status:403,code:'denied',message:'Original denial'})
    await old
    expect(useChatStore.getState().get('first').planMode).toBe(false)
    expect(usePlanModeStore.getState().saves.first).toBe(save)
    expect(write).toHaveBeenCalledTimes(2)
    expect(read).toHaveBeenCalledTimes(scenario === 'readback' ? 1 : 0)
    newer.resolve({ok:true,data:{active:false,pending:null}})
    await current
    expect(usePlanModeStore.getState().saves.first).toBeUndefined()
  })

})
