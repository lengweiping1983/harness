import {afterEach, expect, it, vi} from 'vitest'
import {api} from '../api'
import {noticeText} from '../noticeText'
import {setLocale, t} from '@/i18n'

afterEach(() => { vi.unstubAllGlobals(); setLocale('zh-CN') })

it.each(['other-trace', '', null])('拒绝不匹配的追踪状态身份：%s', async id => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({id, session_id: 'child', status: 'completed'}), {status: 200}))
  vi.stubGlobal('fetch', fetch)
  const result = await api.requestTraceStatus('parent', 'trace')
  expect(result.ok).toBe(false)
  if (result.ok) throw new Error('Expected an identity failure')
  expect(result.code).toBe('response/invalid-schema')
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    expect(noticeText(result.copy!)).toBe(t('ui.http.invalidResponse'))
  }
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('父对话可以读取所属子Agent的匹配追踪，不改写身份或回执', async () => {
  const row = {id: 'trace', session_id: 'child', parent_session_id: 'parent', status: 'started'}
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(row), {status: 200})))
  const result = await api.requestTraceStatus('parent', 'trace')
  expect(result).toEqual({ok: true, data: row})
})

it('保留现有服务端错误，不将拒绝伪装成身份错误', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({code: 'fixture/rejected', error: 'Original error 原文'}), {status: 403})))
  const result = await api.requestTraceStatus('parent', 'trace')
  expect(result).toMatchObject({ok: false, status: 403, code: 'fixture/rejected', message: 'Original error 原文'})
})
