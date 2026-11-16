import {afterEach, expect, test} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {setLocale} from '@/i18n'
import {ErrorBlock} from '@/components/chat/blocks/MessageBlocks'
import {RunHistoryCard} from '@/components/inspector/RunHistoryCard'
import type {RunRecord} from '@/lib/protocol.generated'
import {modelFailure} from '../modelFailure'

afterEach(() => setLocale('zh-CN'))

test('content inspection failures use structured identity and preserve diagnostics in English Chinese and English', async () => {
  const block = {id: 'inspection', at: 0, kind: 'error' as const, turn: 1,
    code: 'llm/provider', message: 'Output data may contain inappropriate content.',
    details: {status: 200, error_code: 'data_inspection_failed', attempts: 1, recoveries: 0}}
  const before = JSON.stringify(block)
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    for (const code of ['llm/provider', 'llm/content-filter']) {
      const title = locale === 'zh-CN' ? '模型内容审核未通过' : 'Model content review rejected'
      const failure = modelFailure(block.message, code, block.details)
      expect(failure?.title).toBe(title)
      expect(failure?.hint).toContain(locale === 'zh-CN' ? '请求及上下文' : 'request and context')
      expect(failure?.hint).not.toContain(locale === 'zh-CN' ? '检查模型连接' : 'Check the model connection')
      const record: RunRecord = {id: 'run', session_id: 'conversation', text: 'Sports animation',
        status: 'failed', created_at: 1, error: {code, message: block.message, details: {...block.details, copy: undefined}}}
      const views = [renderToStaticMarkup(<ErrorBlock block={{...block, code}} sessionId="conversation" />),
        renderToStaticMarkup(<RunHistoryCard record={record} sessionId="conversation" busy={false}
          expanded onContinue={() => {}} onOriginal={() => {}} />)]
      for (const html of views) {
        expect(html).toContain(title)
        expect(html).toContain(block.message)
      }
      expect(views[0]).toContain('data_inspection_failed')
      expect(views[0]).not.toContain('<details open')
    }
    expect(modelFailure(block.message, 'llm/provider')?.title).not.toContain('review rejected')
    expect(modelFailure(block.message, 'llm/provider', {error_code: 'unknown'})?.title)
      .not.toBe(modelFailure(block.message, 'llm/content-filter')?.title)
    expect(modelFailure(block.message, 'tool/error', block.details)).toBeNull()
  }
  expect(JSON.stringify(block)).toBe(before)
})
