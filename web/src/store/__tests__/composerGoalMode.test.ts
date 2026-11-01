import {afterEach,beforeEach, describe, expect, it, vi} from 'vitest'
import {api} from '@/lib/api'
import {modeTaskText} from '@/lib/composerMode'
import {useDraftStore} from '../drafts'
import {useChatStore} from '../chat'
import {useAppStore} from '../app'
import type {GoalState} from '@/types'

afterEach(()=>vi.unstubAllGlobals())

beforeEach(() => {
  const localValues=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>localValues.get(key)??null,setItem:(key:string,value:string)=>localValues.set(key,value),removeItem:(key:string)=>localValues.delete(key)})

  useChatStore.setState({sessions: {}})
  useDraftStore.setState({bySession: {}, submissions: {}})
  useAppStore.setState({activeSessionId: null, activeWorkspaceId: 'project', newSessionCheckout: null, newSessionCheckoutStatus: 'ready', sessionCreating: false, sessions: [], newSessionRevision: 1,
    workspaces: [{id: 'project', path: '/fixture', name: 'Project', color: 'brand'}]})
  vi.restoreAllMocks()
})

describe('目标输入选择与持续目标的边界', () => {
  it('目标任务投递后消费输入选择，完成后的普通消息不创建新目标', () => {
    const store = useDraftStore.getState()
    store.setExecutionMode('current', 'goal')
    store.setText('current', '保留的后续草稿')
    store.setExecutionMode('other', 'goal')
    store.rememberSubmission('current', {id: 'goal-send', text: '/goal 核对结果', attachments: [], action: 'send', status: 'sending'})
    store.consumeExecutionMode('current', 'goal')
    expect(useDraftStore.getState().bySession.current.executionMode).toBeUndefined()
    expect(useDraftStore.getState().bySession.current.text).toBe('保留的后续草稿')
    expect(useDraftStore.getState().bySession.other.executionMode).toBe('goal')
    expect(useDraftStore.getState().submissions.current[0].executionMode).toBe('goal')
    expect(modeTaskText('再解释一下', useDraftStore.getState().bySession.current.executionMode ?? 'normal')).toBe('再解释一下')
  })

  it('当前目标完成或清除时收起其输入标记，保留草稿和新计划选择', () => {
    const active: GoalState = {activation: 'armed', stateVersion: 1, goal: {
      id: 'current-goal', revision: 1, objective: '当前目标', phase: 'active', roundsStarted: 0, maxGoalRounds: null,
    }}
    useChatStore.getState().apply('current', {type: 'goal/state', ...active})
    useDraftStore.getState().setExecutionMode('current', 'goal')
    useDraftStore.getState().setText('current', '未发送补充')
    useChatStore.getState().apply('current', {type: 'goal/state', activation: 'disarmed', stateVersion: 2,
      goal: {...active.goal!, revision: 2, phase: 'complete'}})
    expect(useDraftStore.getState().bySession.current.executionMode).toBeUndefined()
    expect(useDraftStore.getState().bySession.current.text).toBe('未发送补充')
    useDraftStore.getState().setExecutionMode('current', 'goal')
    useChatStore.getState().apply('current', {type: 'goal/state', activation: 'disarmed', stateVersion: 2,
      goal: {...active.goal!, revision: 2, phase: 'complete'}})
    expect(useDraftStore.getState().bySession.current.executionMode).toBe('goal')
    useChatStore.getState().apply('current', {type: 'goal/state', ...active, stateVersion: 3})
    useDraftStore.getState().setExecutionMode('current', 'plan')
    useChatStore.getState().apply('current', {type: 'goal/state', activation: 'disarmed', stateVersion: 4, goal: null})
    expect(useDraftStore.getState().bySession.current.executionMode).toBe('plan')
  })

  it('晚到的目标回执不能消费后来选中的计划，未知提交仍能恢复目标意图', () => {
    const store = useDraftStore.getState()
    store.setExecutionMode('current', 'goal')
    store.rememberSubmission('current', {id: 'goal-send', text: '/goal 核对结果', attachments: [], action: 'send', status: 'unknown'})
    store.setExecutionMode('current', 'plan')
    store.consumeExecutionMode('current', 'goal')
    expect(useDraftStore.getState().bySession.current.executionMode).toBe('plan')
    store.setExecutionMode('current')
    expect(store.restoreSubmission('current', 'goal-send')).toBe(true)
    expect(useDraftStore.getState().bySession.current.executionMode).toBe('goal')
  })

  it('新会话打开目标设置只物化空会话，不排队任务且保留目标选择', async () => {
    vi.spyOn(api, 'createSession').mockImplementationOnce(async(_body,id)=>({ok: true, data: {session: {id: 'prepared',metadata:{creation_request_id:id,creation_completed:true}}}}))
    useDraftStore.getState().setExecutionMode('__unsaved__', 'goal')
    expect(await useAppStore.getState().materializeSession()).toBe(true)
    expect(useAppStore.getState().activeSessionId).toBe('prepared')
    expect(useDraftStore.getState().submissions.prepared).toBeUndefined()
    expect(useDraftStore.getState().bySession.prepared.executionMode).toBe('goal')
  })

  it('空会话创建的迟到响应不能覆盖后来选择的计划或打开旧目标表单', async () => {
    let resolve!: (value: Awaited<ReturnType<typeof api.createSession>>) => void
    vi.spyOn(api, 'createSession').mockReturnValueOnce(new Promise(done => {resolve = done}))
    useDraftStore.getState().setExecutionMode('__unsaved__', 'goal')
    const pending = useAppStore.getState().materializeSession()
    useDraftStore.getState().setExecutionMode('__unsaved__', 'plan')
    useDraftStore.getState().setText('__unsaved__', '保留新的规划草稿')
    resolve({ok: true, data: {session: {id: 'prepared',metadata:{creation_request_id:vi.mocked(api.createSession).mock.calls[0][1],creation_completed:true}}}})
    await pending
    expect(useAppStore.getState().activeSessionId).toBeNull()
    expect(useDraftStore.getState().bySession.__unsaved__.executionMode).toBe('plan')
    expect(useDraftStore.getState().bySession.__unsaved__.text).toBe('保留新的规划草稿')
    expect(useDraftStore.getState().submissions.prepared).toBeUndefined()
  })
})
