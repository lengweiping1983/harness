import {beforeEach, describe, expect, it, vi} from 'vitest'
vi.mock('@/lib/api', () => ({api: {controlGoal: vi.fn(), goal: vi.fn()}}))
import {api} from '@/lib/api'
import {controlGoal} from '@/lib/goalControl'
import {useChatStore} from '@/store/chat'
import type {GoalState} from '@/types'

const write = vi.mocked(api.controlGoal), read = vi.mocked(api.goal)
const state = (revision = 1, phase: NonNullable<GoalState['goal']>['phase'] = 'active'): GoalState => ({
  stateVersion: revision, activation: phase === 'active' ? 'armed' : 'disarmed',
  goal: {id: 'goal-current', revision, objective: '核对当前目标', criteria: ['核对当前目标'], phase, roundsStarted: 1, maxGoalRounds: null},
})
beforeEach(() => {vi.clearAllMocks(); useChatStore.setState({sessions: {}})})

describe('目标控制的未知写入与回查', () => {
  it('暂停回查保留状态，但不将状态保存等同整次控制成功', async () => {
    write.mockResolvedValueOnce({ok: false, status: 0, code: 'network', message: '回执丢失'})
    read.mockResolvedValueOnce({ok: true, data: state(2, 'paused')})
    await expect(controlGoal('current', {action: 'pause', goal_id: 'goal-current', revision: 1})).rejects.toThrow('回执丢失')
    expect(useChatStore.getState().get('current').goalState.goal?.phase).toBe('paused')
    expect(write).toHaveBeenCalledTimes(1)
    expect(read).toHaveBeenCalledExactlyOnceWith('current')
  })

  it('新建回执丢失只能确认新的确切目标，不能认领原目标或另一目标', async () => {
    for (const next of [state(), {...state(2), goal: {...state(2).goal!, id: 'goal-new', objective: '另一个目标'}}]) {
      write.mockResolvedValueOnce({ok: false, status: 0, code: 'timeout', message: '回执未知'})
      read.mockResolvedValueOnce({ok: true, data: next})
      await expect(controlGoal('current', {action: 'create', objective: '核对当前目标', max_goal_rounds: null})).rejects.toThrow('回执未知')
    }
    expect(write).toHaveBeenCalledTimes(2)
  })

  it('失败后发布回查状态，较晚回查不能覆盖更新的暂停状态', async () => {
    write.mockResolvedValueOnce({ok: false, status: 503, code: 'unavailable', message: '服务失败'})
    read.mockImplementationOnce(async () => {
      useChatStore.getState().apply('current', {type: 'goal/state', ...state(3, 'paused')})
      return {ok: true, data: state(2)}
    })
    await expect(controlGoal('current', {action: 'resume', goal_id: 'goal-current', revision: 1})).rejects.toThrow('服务失败')
    expect(useChatStore.getState().get('current').goalState.goal?.phase).toBe('paused')
    expect(useChatStore.getState().get('current').goalState.stateVersion).toBe(3)
  })

  it('新建目标已保存也不能证明控制后续步骤完成', async () => {
    const next = {...state(2), goal: {...state(2).goal!, id: 'goal-new', tokenBudget: 500}}
    write.mockResolvedValue({ok: false, status: 0, code: 'network', message: '回执丢失'})
    read.mockResolvedValue({ok: true, data: next})
    await expect(controlGoal('current', {action: 'create', objective: '核对当前目标', max_goal_rounds: null, token_budget: 500})).rejects.toThrow('回执丢失')
    expect(useChatStore.getState().get('current').goalState.goal?.id).toBe('goal-new')
    await expect(controlGoal('current', {action: 'create', objective: '核对当前目标', token_budget: 1000})).rejects.toThrow('回执丢失')
    expect(write).toHaveBeenCalledTimes(2)
  })

  it('确定的版本冲突不会被相似的回查状态当成成功', async () => {
    write.mockResolvedValueOnce({ok: false, status: 409, code: 'GOAL_STALE_REVISION', message: '版本冲突'})
    read.mockResolvedValueOnce({ok: true, data: state(2, 'paused')})
    await expect(controlGoal('current', {action: 'pause', goal_id: 'goal-current', revision: 1})).rejects.toThrow('版本冲突')
    expect(write).toHaveBeenCalledTimes(1)
    expect(useChatStore.getState().get('current').goalState.goal?.phase).toBe('paused')
  })
})

it('HTTP408 reads saved state without confirming the whole operation or replaying control', async () => {
  write.mockResolvedValueOnce({ok:false,status:408,code:'http/408',message:'Unknown timeout'})
  read.mockResolvedValueOnce({ok:true,data:state(2,'paused')})
  await expect(controlGoal('current',{action:'pause',goal_id:'goal-current',revision:1})).rejects.toThrow('Unknown timeout')
  expect(useChatStore.getState().get('current').goalState.goal?.phase).toBe('paused')
  expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
})
