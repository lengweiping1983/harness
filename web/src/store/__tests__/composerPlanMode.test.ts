import {beforeEach, describe, expect, it} from 'vitest'
import {modeTaskText, resolveComposerMode} from '@/lib/composerMode'
import {useDraftStore} from '../drafts'
import {useChatStore} from '../chat'

beforeEach(() => {
  useChatStore.setState({sessions: {}})
  useDraftStore.setState({bySession: {}, submissions: {}})
})

describe('计划输入选择与会话模式的生命周期', () => {
  it('计划中发送补充正文不重复开启计划，显式命令保持原意', () => {
    expect(modeTaskText('补充需求', 'plan', false, true)).toBe('补充需求')
    expect(modeTaskText('分析新需求', 'plan', false, false)).toBe('/plan 分析新需求')
    expect(modeTaskText('/plan 新计划', 'plan', false, true)).toBe('/plan 新计划')
  })

  it('提交后消费计划选择，批准后恢复普通输入，草稿与其他会话保留', () => {
    const store = useDraftStore.getState()
    store.setText('current', '未发送的新草稿')
    store.setExecutionMode('current', 'plan')
    store.setExecutionMode('other', 'plan')
    store.rememberSubmission('current', {id: 'request', text: '/plan 分析', attachments: [], action: 'send', status: 'sending'})
    store.consumeExecutionMode('current', 'plan')
    expect(useDraftStore.getState().bySession.current.executionMode).toBeUndefined()
    expect(useDraftStore.getState().bySession.current.text).toBe('未发送的新草稿')
    expect(useDraftStore.getState().bySession.other.executionMode).toBe('plan')
    expect(useDraftStore.getState().submissions.current[0].executionMode).toBe('plan')
    useChatStore.getState().setPlanMode('current', {active: true, pending: null})
    expect(resolveComposerMode(undefined, true)).toBe('plan')
    useChatStore.getState().setPlanMode('current', {active: false, pending: null})
    expect(resolveComposerMode(useDraftStore.getState().bySession.current.executionMode, false)).toBe('normal')
  })

  it('普通草稿不能遮蔽服务端仍有效的计划限制，目标选择仍互斥', () => {
    expect(resolveComposerMode('normal', true)).toBe('plan')
    expect(resolveComposerMode('goal', true)).toBe('goal')
    expect(resolveComposerMode('plan', false)).toBe('plan')
  })

  it('目标选择不被消费，未知提交的计划意图可以显式恢复', () => {
    const store = useDraftStore.getState()
    store.setExecutionMode('goal', 'goal')
    store.consumeExecutionMode('goal', 'plan')
    expect(useDraftStore.getState().bySession.goal.executionMode).toBe('goal')
    store.setExecutionMode('plan', 'plan')
    store.rememberSubmission('plan', {id: 'request', text: '待恢复的任务', attachments: [], action: 'send', status: 'unknown'})
    store.consumeExecutionMode('plan', 'plan')
    expect(store.restoreSubmission('plan', 'request')).toBe(true)
    expect(useDraftStore.getState().bySession.plan.executionMode).toBe('plan')
  })
})
