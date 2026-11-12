import { afterEach, expect, it } from 'vitest'
import { setLocale, t } from '@/i18n'
import { blocksFromDetail, emptySession, reduceEvent, useChatStore } from '@/store/chat'
import type { NoticeBlock, SessionDetail, StreamEvent } from '@/types'
import { noticeText } from '../noticeText'
import { permissionDecision } from '../permissions'

afterEach(() => { setLocale('zh-CN'); useChatStore.setState({ sessions: {} }) })

it('stored goal, approval and interruption notices use the current language without rebuilding state', () => {
  setLocale('en')
  const detail: SessionDetail = { header: { id: 'fixture' }, events: [], usage: {}, stats: {}, todos: [], timeline: [],
    events_view: 'conversation-window', interrupted: true, messages: [
      { kind: 'message', role: 'user', source: 'goal', source_seq: 1, turn: 1, text: 'Private goal 原文' },
      { kind: 'message', role: 'tool', source_seq: 2, turn: 1, results: [
        { tool_use_id: 'call', name: 'FixtureTool', content: 'Original output 原文', approval_decision: ['allow'], approval_reason: 'Original reason 原文', approval_reviewer: 'auto_review' },
      ] },
    ] }
  useChatStore.getState().hydrate('fixture', detail)
  const blocks = useChatStore.getState().get('fixture').blocks
  const notices = blocks.filter((block): block is NoticeBlock => block.kind === 'notice')
  expect(notices).toHaveLength(3)
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    expect(useChatStore.getState().get('fixture').blocks).toBe(blocks)
    expect(noticeText(notices[0]!.text)).toBe(t('ui.store.chat.64f873358223'))
    expect(noticeText(notices[1]!.text)).toBe(t('ui.store.chat.c36db9f5daed', { p0: permissionDecision(['allow'], 'auto_review', 'Original reason 原文'), p1: 'FixtureTool' }))
    expect(noticeText(notices[2]!.text)).toBe(t('ui.store.chat.701db4b527c4'))
    expect(noticeText('Original content 原文')).toBe('Original content 原文')
  }
  expect(blocksFromDetail(detail).find(block => block.kind === 'tool')?.output).toBe('Original output 原文')
})

it('live goal continuation also retains resource identity', () => {
  setLocale('en')
  const patch = reduceEvent(emptySession(), { type: 'user/message', source: 'goal', text: 'private' } as StreamEvent)
  const block = patch.blocks?.[0] as NoticeBlock
  expect(block.text).toEqual({ key: 'ui.store.chat.64f873358223' })
  setLocale('zh-CN')
  expect(noticeText(block.text)).toBe(t('ui.store.chat.64f873358223'))
})
