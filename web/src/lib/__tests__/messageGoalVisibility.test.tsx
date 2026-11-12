import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it, vi} from 'vitest'
import {setLocale, t} from '@/i18n'
import {UserBlock} from '@/components/chat/blocks/MessageBlocks'

const {sessions} = vi.hoisted(() => ({sessions: {} as Record<string, any>}))
vi.mock('@/store/chat', async importOriginal => {
  const actual = await importOriginal<typeof import('@/store/chat')>()
  return {...actual, useChatStore: Object.assign(
  (selector: (state: any) => unknown) => selector({sessions}),
  {getState: () => ({sessions}), subscribe: actual.useChatStore.subscribe},
)}
})

afterEach(() => {delete sessions.fixture; setLocale('zh-CN')})

it('普通模式提供设为目标，计划模式及未完成目标隐藏入口，完成后恢复', () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    for (const planMode of [false, true]) {
      for (const phase of [null, 'active', 'paused', 'blocked', 'complete']) {
        sessions.fixture = {planMode, goalState: {goal: phase ? {phase} : null}}
        const html = renderToStaticMarkup(<UserBlock sessionId="fixture" block={{kind:'user', id:'message', at:1, text:'Original objective', sourceSeq:1}} />)
        expect(html.includes(`aria-label="${t('ui.goal.setFromMessage')}"`)).toBe(!planMode && (phase === null || phase === 'complete'))
      }
    }
  }
})

it('隐藏创建入口时保留已有目标标识及其他消息操作', () => {
  sessions.fixture = {planMode:true, goalState:{goal:{phase:'active'}}}
  const html = renderToStaticMarkup(<UserBlock sessionId="fixture" block={{kind:'user', id:'message', at:1, text:'Original objective', sourceSeq:1, goalId:'existing-goal'}} />)
  expect(html).toContain('data-goal-id="existing-goal"')
  expect(html).toContain(`aria-label="${t('ui.messageEdit.action')}"`)
  expect(html).not.toContain(`aria-label="${t('ui.goal.setFromMessage')}"`)
})
