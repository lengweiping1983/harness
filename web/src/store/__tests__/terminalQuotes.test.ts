import {beforeEach, describe, expect, it} from 'vitest'
import {useAppStore} from '../app'

beforeEach(() => useAppStore.setState({activeSessionId: 'first', pendingQuote: null, layoutMode: 'overlay', drawerIntent: 'right'}))

describe('终端摘录的会话归属', () => {
  it('摘录明确属于终端来源会话，并关闭该会话的窄屏面板', () => {
    useAppStore.getState().quoteInto('first', '终端输出')
    expect(useAppStore.getState().pendingQuote).toMatchObject({sessionId: 'first', text: '终端输出'})
    expect(useAppStore.getState().drawerIntent).toBe('none')
  })

  it('旧终端的延迟操作不能向另一个会话投递或关闭面板', () => {
    useAppStore.setState({activeSessionId: 'second'})
    useAppStore.getState().quoteInto('first', '旧会话输出')
    expect(useAppStore.getState().pendingQuote).toBeNull()
    expect(useAppStore.getState().drawerIntent).toBe('right')
  })

  it('切换会话或打开新草稿时撤销尚未消费的摘录', () => {
    useAppStore.getState().quoteInto('first', '旧终端输出')
    useAppStore.getState().selectSession('second')
    expect(useAppStore.getState().pendingQuote).toBeNull()
    useAppStore.getState().quoteInto('second', '第二会话输出')
    useAppStore.getState().startNewSession()
    expect(useAppStore.getState().pendingQuote).toBeNull()
  })

  it('较早的消费回执不能清除后来投递的摘录', () => {
    useAppStore.getState().quoteInto('first', '第一份输出')
    const previous = useAppStore.getState().pendingQuote!
    useAppStore.getState().quoteInto('first', '第二份输出')
    useAppStore.getState().clearQuote(previous.seq)
    expect(useAppStore.getState().pendingQuote?.text).toBe('第二份输出')
    useAppStore.getState().clearQuote(useAppStore.getState().pendingQuote!.seq)
    expect(useAppStore.getState().pendingQuote).toBeNull()
  })
})
