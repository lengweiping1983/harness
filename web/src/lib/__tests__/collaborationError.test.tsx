import {afterEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {setLocale, t} from '@/i18n'
import {request} from '../http'
import {collaboration} from '../collaboration'
import {collaborationErrorMessage, collaborationFeedbackMessage} from '../collaborationError'
import {ErrorBlock} from '@/components/chat/blocks/MessageBlocks'

afterEach(() => {setLocale('zh-CN'); vi.unstubAllGlobals(); vi.useRealTimers()})

it('renders missing conversation questions in English Chinese English without repeating the read', async () => {
  const code = 'session/not-found'
  const fetch = vi.fn().mockResolvedValue(Response.json({code, error: 'Original server snapshot'}, {status: 403}))
  vi.stubGlobal('fetch', fetch)
  const result = await collaboration.questions('missing')
  if (result.ok) throw Error('Expected missing conversation')
  expect(result.code).toBe(code)
  expect(result.status).toBe(403)
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const message = collaborationFeedbackMessage(result)
    expect(message).toBe(t('ui.collaboration.error.session/not-found'))
    expect(message).not.toContain(code)
    expect(message).not.toContain('Original server snapshot')
  }
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/v1/sessions/missing/questions', expect.anything())
})

it('resolves saved product feedback in the reading language and preserves third-party text', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({code: 'access/denied', error: '宿主固定语言'}), {status: 403})))
  setLocale('en')
  const result = await request('/transport-test')
  expect(result.ok).toBe(false)
  if (result.ok) throw new Error('expected refusal')
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    expect(collaborationFeedbackMessage(result)).toBe(t('ui.collaboration.error.access/denied', {code: 'access/denied'}))
    expect(collaborationFeedbackMessage({key: 'ui.shared.submitUnknown'})).toBe(t('ui.shared.submitUnknown'))
    expect(collaborationErrorMessage('third-party/failure', 'Original 原文')).toBe('Original 原文')
  }
})

it('renders shared error frames in the viewer language without exposing private diagnostics', () => {
  const block = {id: 'error', at: 0, kind: 'error' as const, code: 'provider/unavailable', message: 'private diagnostic 原文'}
  for (const language of ['en', 'zh-CN'] as const) {
    setLocale(language)
    const shared = renderToStaticMarkup(<ErrorBlock block={block} readOnly />)
    expect(shared).toContain(t('ui.collaboration.operation_failed', {code: block.code}))
    expect(shared).not.toContain('private diagnostic')
    expect(renderToStaticMarkup(<ErrorBlock block={block} />)).toContain('private diagnostic 原文')
  }
})

it('preserves transport resources in saved visitor feedback without exposing an old-language snapshot', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not json')))
  setLocale('en')
  const result = await request('/transport-test')
  if (result.ok) throw Error('Expected invalid response')
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    expect(collaborationFeedbackMessage(result)).toBe(t('ui.http.invalidResponse'))
  }
})

it.each(['state', 'queue'] as const)('shared %s reads propagate cancellation without another request', async method => {
  vi.useFakeTimers()
  let signal: AbortSignal | null = null
  const fetch = vi.fn((_path: RequestInfo | URL, options?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    signal = options?.signal ?? null
    signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {once: true})
  }))
  vi.stubGlobal('fetch', fetch)
  const controller = new AbortController()
  const pending = collaboration[method]('original-session', controller.signal)
  expect(fetch).toHaveBeenCalledTimes(1)
  controller.abort()
  expect(await pending).toMatchObject({ok: false, status: 0, code: 'cancelled'})
  expect(signal!.aborted).toBe(true)
  expect(fetch.mock.calls[0]![0]).toBe('/api/v1/sessions/original-session/' + (method === 'state' ? 'collaboration' : 'queue'))
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})
