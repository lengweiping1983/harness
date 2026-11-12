import {afterEach, expect, it} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {setLocale, t} from '@/i18n'
import {ErrorBlock as ErrorCard} from '@/components/chat/blocks/MessageBlocks'
import {blocksFromDetail, emptySession, reduceEvent} from '../chat'
import type {ErrorBlock, SessionDetail, StreamEvent} from '@/types'

afterEach(() => setLocale('zh-CN'))

const apply = (events: StreamEvent[]) => events.reduce((state, event) => ({...state, ...reduceEvent(state, event)}), {...emptySession(), turn: 2, running: true})

it.each(['llm/request', 'llm/timeout', 'llm/response', 'llm/context-length', 'llm/provider-model-call-failed', 'agent/step-limit', 'agent/token-budget', 'session/persistence', 'run/error'])('%s converges terminal and runtime errors without losing diagnostics', code => {
  const error = {code, message: 'Original diagnostic 原文 {value}', details: {status: 503, cause: 'Original cause 原因'}}
  const terminal = {type: 'turn/end', turn: 2, reason: 'error', error: `[${code}] ${error.message} {'status': 503}`, ...error, source_seq: 12, time: 20}
  const live = {type: 'error', ...error}
  const snapshot = {header: {id: 'fixture'}, messages: [], events: [{type: 'turn/end', seq: 12, time: 20,
    data: {turn: 2, reason: {kind: 'error', error}}}]} as unknown as SessionDetail
  const restored = blocksFromDetail(snapshot).filter(block => block.kind === 'error') as ErrorBlock[]
  expect(restored).toHaveLength(1)
  for (const events of [[terminal, {type: 'run/state', status: 'failed'}, {type: 'done'}, live, live, terminal], [live, terminal, live]]) {
    const state = apply(events)
    const failures = state.blocks.filter(block => block.kind === 'error') as ErrorBlock[]
    expect(failures).toHaveLength(1)
    expect(failures[0]).toMatchObject({turn: 2, code, message: error.message, details: error.details, sourceSeq: 12})
    expect(state.turnActivity[2]).toMatchObject({endSeq: 12, endedAt: 20, reason: 'error'})
    for (const language of ['en', 'zh-CN', 'en'] as const) {
      setLocale(language)
      for (const block of [failures[0]!, restored[0]!]) {
        const html = renderToStaticMarkup(<ErrorCard block={block} sessionId="fixture" />)
        expect(html).toContain(error.message)
        expect(html).toContain(error.details.cause)
        expect(html).toContain('503')
        expect(html).toContain(t('ui.components.chat.blocks.MessageBlocks.c26fb419db3c'))
        expect(html).not.toContain('<details open')
      }
      const publicCard = renderToStaticMarkup(<ErrorCard block={failures[0]!} readOnly />)
      expect(publicCard).not.toContain(error.message)
      expect(publicCard).not.toContain(error.details.cause)
    }
  }
})

it.each(['code', 'message', 'details', 'turn'] as const)('keeps independent errors with different %s', field => {
  const original = {code: 'llm/request', message: 'Original diagnostic', details: {status: 503}}
  const changed = {...original, [field]: field === 'code' ? 'llm/timeout' : field === 'message' ? 'Another diagnostic' : field === 'details' ? {status: 429} : 3}
  const state = apply([{type: 'turn/end', turn: 2, reason: 'error', ...original},
    ...(field === 'turn' ? [{type: 'turn/start', turn: 3}] : []), {type: 'error', ...changed}])
  expect(state.blocks.filter(block => block.kind === 'error')).toHaveLength(2)
})

it('converges exact code-prefixed diagnostics in either event order', () => {
  const live = {type: 'error', code: 'llm/request', message: 'Original diagnostic'}
  const end = {type: 'turn/end', turn: 2, reason: 'error', error: '[llm/request] Original diagnostic'}
  for (const events of [[live, end], [end, live]]) {
    expect(apply(events).blocks.filter(block => block.kind === 'error')).toHaveLength(1)
  }
})
