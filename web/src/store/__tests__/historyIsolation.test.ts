import {afterEach, expect, it, vi} from 'vitest'
import {useChatStore} from '../chat'
import * as http from '@/lib/http'
import type {SessionDetail} from '@/types'

const detail = (text: string, start = 10, end = 20): SessionDetail => ({
  header: {id: 'same'}, messages: [{kind: 'message', role: 'user', text, source_seq: start}],
  events: [], usage: {}, todos: [], stats: {}, timeline: [], events_view: 'conversation-window',
  history: {through: 20, start, end, total: 20, has_more: start > 0, next_cursor: start > 0 ? 'same-cursor' : null, user_messages: []},
})
afterEach(() => {vi.restoreAllMocks();useChatStore.setState({sessions: {}})})

it.each(['success', 'failure'] as const)('rejects an old page %s after dropping and reopening the same session', async outcome => {
  let resolve!: (value: SessionDetail) => void, reject!: (error: Error) => void
  vi.spyOn(http, 'runtimeRequest').mockImplementation(() => new Promise((yes, no) => {resolve = yes as typeof resolve;reject = no}))
  useChatStore.getState().hydrate('same', detail('original'))
  const pending = useChatStore.getState().loadOlder('same')
  useChatStore.getState().drop('same')
  useChatStore.getState().hydrate('same', detail('reopened'))
  const before = useChatStore.getState().get('same')
  if (outcome === 'success') resolve(detail('obsolete', 0, 10))
  else reject(new Error('obsolete failure'))
  await pending
  expect(useChatStore.getState().get('same')).toBe(before)
})

it('keeps normal paging and pending-page duplicate admission intact', async () => {
  let resolve!: (value: SessionDetail) => void
  const request = vi.spyOn(http, 'runtimeRequest').mockImplementation(() => new Promise(yes => {resolve = yes as typeof resolve}))
  useChatStore.getState().hydrate('same', detail('current'))
  const pending = useChatStore.getState().loadOlder('same')
  await useChatStore.getState().loadOlder('same')
  expect(request).toHaveBeenCalledTimes(1)
  resolve(detail('older', 0, 10))
  await pending
  const state = useChatStore.getState().get('same')
  expect(state.blocks.filter(block => block.kind === 'user').map(block => block.text)).toEqual(['older', 'current'])
  expect(state.history?.loading).toBe(false)
  expect(state.history?.has_more).toBe(false)
})
