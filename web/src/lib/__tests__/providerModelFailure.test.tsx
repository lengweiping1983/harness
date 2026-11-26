import {afterEach, expect, test} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {setLocale, t} from '@/i18n'
import {ErrorBlock} from '@/components/chat/blocks/MessageBlocks'
import {modelFailure} from '../modelFailure'

afterEach(() => setLocale('zh-CN'))

test('model call failures explain the provider boundary in English Chinese and English', () => {
  const original = '409: {"code":"MODEL_CALL_FAILED","traceId":"trace-fixture"}'
  const views = []
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    const view = modelFailure(original, 'llm/provider-model-call-failed')
    expect(view?.title).toBe(locale === 'zh-CN' ? '提供商模型调用失败' : 'Provider model call failed')
    expect(view?.hint).toContain('traceId')
    expect(view?.hint).toContain(locale === 'zh-CN' ? '服务商' : 'provider')
    expect(view?.hint).toContain(locale === 'zh-CN' ? '已有执行任务已保留' : 'Existing execution tasks have been preserved')
    expect(modelFailure(original, 'llm/timeout')).not.toEqual(view)
    views.push(view)
  }
  expect(views[0]).toEqual(views[2])
  expect(views[0]).not.toEqual(views[1])
  expect(original).toBe('409: {"code":"MODEL_CALL_FAILED","traceId":"trace-fixture"}')
})

test('the actual error card retains provider diagnostics across language changes', () => {
  const block = {id: 'failure', at: 0, kind: 'error' as const, turn: 3,
    code: 'llm/provider-model-call-failed', message: 'Original provider diagnostic 原文',
    details: {status: 409, error_code: 'MODEL_CALL_FAILED', provider_trace_id: 'trace-fixture', recoveries: 0}}
  const before = JSON.stringify(block)
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    const html = renderToStaticMarkup(<ErrorBlock block={block} sessionId="original-conversation" />)
    expect(html).toContain(locale === 'zh-CN' ? '提供商模型调用失败' : 'Provider model call failed')
    expect(html).toContain(locale === 'zh-CN' ? '服务商核查' : 'provider for investigation')
    expect(html).toContain('Original provider diagnostic 原文')
    expect(html).toContain('trace-fixture')
    expect(html).toContain('MODEL_CALL_FAILED')
    expect(html).not.toContain('<details open')
  }
  expect(JSON.stringify(block)).toBe(before)
})

test('a missing model card directs the user to select a model in both languages', async () => {
  const block = {id: 'missing', at: 0, kind: 'error' as const, turn: 1,
    code: 'llm/model-missing', message: 'Original diagnostic', details: {}}
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const html = renderToStaticMarkup(<ErrorBlock block={block} sessionId="conversation" />)
    expect(html).toContain(t('ui.models.unavailableName'))
    expect(html).toContain(t('models.model.missing'))
    expect(html).toContain('llm/model-missing')
    expect(html).toContain('Original diagnostic')
    expect(html).not.toContain(t('ui.modelFailure.requestHint'))
  }
})

test('subscription admission errors show the recorded reason or account settings guidance', async () => {
  const block = {id: 'quota', at: 0, kind: 'error' as const, turn: 1, code: 'llm/chatgpt-account', message: 'Saved diagnostic'}
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    for (const reason of ['quotaUnavailable', 'quotaStale', 'windowUnknown', 'authUnavailable', 'allowanceBlocked', 'rateLimited']) {
      const key = 'ui.codexGateway.' + reason
      const html = renderToStaticMarkup(<ErrorBlock block={{...block, details: {copy: {key}}}} sessionId="conversation" />)
      expect(html).toContain(t('ui.codexGateway.accountUnavailableTitle'))
      expect(html).toContain(t(key))
      expect(html).not.toContain(t('ui.modelFailure.requestHint'))
    }
    expect(renderToStaticMarkup(<ErrorBlock block={block} sessionId="conversation" />)).toContain(t('ui.codexGateway.checkAccounts'))
  }
})
