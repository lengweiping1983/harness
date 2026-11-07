import {api} from '../api'
import {setLocale, t} from '@/i18n'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {ApiError, pluginRequest, request, runtimeRequest, unwrap} from '../http'

const fetchMock = vi.fn<typeof fetch>()

it.each([
  ['bad-json', 'ui.http.invalidJsonRequest', {}],
  ['session/invalid-field', 'ui.session.titleTooLong', {limit: 512}],
  ['session/invalid-field', 'ui.session.unknownField', {field: 'Original 原文'}],
  ['voice/invalid', 'ui.voice.invalidPreparation', {}],
] as const)('keeps request rejection %s/%s translatable without replay', async (code, key, params) => {
  fetchMock.mockResolvedValue(Response.json({error: 'Original server snapshot', code, details: {copy: {key, params}}}, {status: 400}))
  const result = await api.createSession({title: 'Original'})
  expect(result.ok).toBe(false)
  if (result.ok) throw Error('Expected rejection')
  expect(result.copy).toEqual({key, params})
  const value = failureText(new ApiError(result))
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    expect(noticeText(value)).toBe(t(key, params))
  }
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('does not trust unrelated or malformed request error resources', async () => {
  for (const copy of [{key: 'ui.session.titleTooLong', params: {limit: '512'}},
      {key: 'ui.session.unknownField', params: {field: 'x', extra: 'y'}},
      {key: 'ui.voice.invalidPreparation'}, {key: 'constructor'}]) {
    fetchMock.mockResolvedValueOnce(Response.json({error: 'Original snapshot', code: 'session/invalid-field', details: {copy}}, {status: 400}))
    expect(await api.createSession()).toMatchObject({ok: false, code: 'session/invalid-field', message: 'Original snapshot'})
  }
  expect(fetchMock).toHaveBeenCalledTimes(4)
})

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function awaitAbort() {
  fetchMock.mockImplementation((_path, options) => new Promise((_resolve, reject) => {
    const signal = options!.signal!
    if (signal.aborted) reject(new DOMException('Aborted', 'AbortError'))
    else signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {once: true})
  }))
}

describe('共享 HTTP 传输边界', () => {
  it('保留调用方头部并解析成功响应，完成后释放超时', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({value: 1})))
    const result = await request('/transport-test', {method: 'PATCH', body: '{}', headers: {'X-Test': 'owned'}})
    expect(result).toEqual({ok: true, data: {value: 1}})
    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers)
    expect(headers.get('X-Test')).toBe('owned')
    expect(headers.get('Accept')).toBe('application/json')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('保留服务端结构化错误，不将失败解释为成功', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({code: 'settings/persist-failed', message: '未保存', details: {field: 'language'}}), {status: 500}))
    expect(await request('/transport-test')).toMatchObject({ok: false, status: 500, code: 'settings/persist-failed', message: '未保存', details: {field: 'language'}})
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('拒绝成功状态中的非法 JSON 和不符合公共协议的正文', async () => {
    fetchMock.mockResolvedValueOnce(new Response('not json'))
    expect(await request('/transport-test')).toMatchObject({ok: false, code: 'response/invalid-json'})
    fetchMock.mockResolvedValueOnce(new Response('{}'))
    expect(await request('/api/v1/sessions')).toMatchObject({ok: false, code: 'response/invalid-schema'})
    expect(vi.getTimerCount()).toBe(0)
  })

  it('按轮次及运行标识读取都验证完整运行契约，不接受部分成功回执', async () => {
    const record = {id: 'run', session_id: 'owner', text: 'Original 用户原文', status: 'completed', created_at: 1}
    for (const path of ['/api/v1/sessions/owner/turns/2/run', '/api/v1/sessions/owner/runs/run']) {
      for (const key of ['id', 'session_id', 'text', 'status', 'created_at'] as const) {
        const partial = {...record}; delete (partial as Partial<typeof record>)[key]
        fetchMock.mockResolvedValueOnce(Response.json(partial))
        expect(await request(path)).toMatchObject({ok: false, code: 'response/invalid-schema'})
      }
      fetchMock.mockResolvedValueOnce(Response.json({...record, status: 'invented'}))
      expect(await request(path)).toMatchObject({ok: false, code: 'response/invalid-schema'})
      fetchMock.mockResolvedValueOnce(Response.json(record))
      expect(await request(path)).toEqual({ok: true, data: record})
    }
    expect(fetchMock).toHaveBeenCalledTimes(14); expect(vi.getTimerCount()).toBe(0)
  })

  it('写入超时只返回未知结果，不重发请求', async () => {
    awaitAbort()
    const pending = request('/transport-test', {method: 'POST', body: '{}', timeout: 50})
    await vi.advanceTimersByTimeAsync(50)
    expect(await pending).toMatchObject({ok: false, status: 0, code: 'timeout'})
    await vi.advanceTimersByTimeAsync(1000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('设置读取与保存拒绝缺少必要字段的成功响应', async () => {
    for (const path of ['/api/v1/ui-settings', '/api/v1/settings']) {
      for (const method of ['GET', 'PATCH']) {
        fetchMock.mockResolvedValueOnce(new Response('{}'))
        expect(await request(path, {method})).toMatchObject({ok: false, code: 'response/invalid-schema'})
      }
    }
  })

  it('终端类型与会话列表拒绝缺失字段和错误类型的成功响应', async () => {
    const catalog = {backends: ['cmd', 'powershell'], default_type: 'cmd', enabled: true, managed_runtime: false}
    for (const path of ['/api/v1/terminal/types', '/api/v1/sessions/terminal-owner/terminals']) {
      const valid = path.endsWith('/types') ? catalog : {...catalog, terminals: []}
      for (const invalid of [{}, {...valid, backends: null}, {...valid, backends: [1]}, {...valid, enabled: 'true'}]) {
        fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(invalid)))
        expect(await request(path)).toMatchObject({ok: false, code: 'response/invalid-schema'})
      }
      fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(valid)))
      expect(await request(path)).toEqual({ok: true, data: valid})
      const disabled = {...valid, enabled: false, backends: []}
      fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(disabled)))
      expect(await request(path)).toEqual({ok: true, data: disabled})
    }
  })

  it('设置契约接受当前值及可空字段，拒绝错误类型和枚举', async () => {
    const replies = [
      {path: '/api/v1/ui-settings', valid: {settings: {
        deepseek_web_enabled: false, deepseek_web_api_key_configured: false,
        brave_web_enabled: false, brave_web_api_key_configured: false,
      }}, invalid: {settings: {deepseek_web_enabled: 'false', deepseek_web_api_key_configured: false,
        brave_web_enabled: false, brave_web_api_key_configured: false}}},
      {path: '/api/v1/settings', valid: {config: {
        context_auto_manage: true, context_working_budget: 384000, summary_max_tokens: null,
        output_reserve: 0, context_window: null, tool_result_max_chars: null, tool_schema_mode: 'auto',
      }, effective: {}}, invalid: {config: {
        context_auto_manage: true, context_working_budget: 384000, summary_max_tokens: null,
        output_reserve: 0, context_window: null, tool_result_max_chars: null, tool_schema_mode: 'invalid',
      }, effective: {}}},
    ]
    for (const {path, valid, invalid} of replies) {
      for (const method of ['GET', 'PATCH']) {
        fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(valid)))
        expect(await request(path, {method})).toEqual({ok: true, data: valid})
        fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(invalid)))
        expect(await request(path, {method})).toMatchObject({ok: false, code: 'response/invalid-schema'})
      }
    }
  })

  it('调用方取消与超时区分，并移除取消监听', async () => {
    awaitAbort()
    const controller = new AbortController()
    const removed = vi.spyOn(controller.signal, 'removeEventListener')
    const pending = request('/transport-test', {signal: controller.signal})
    controller.abort()
    expect(await pending).toMatchObject({ok: false, status: 0, code: 'cancelled'})
    expect(removed).toHaveBeenCalledWith('abort', expect.any(Function))
    expect(vi.getTimerCount()).toBe(0)
  })

  it('连接失败只报告一次，unwrap 保留错误码与详情', async () => {
    fetchMock.mockRejectedValue(new TypeError('offline'))
    expect(await request('/transport-test', {method: 'PATCH', body: '{}'})).toMatchObject({ok: false, code: 'network'})
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const failure = {ok: false as const, status: 409, code: 'conflict', message: '已变化', details: {version: 2}}
    const error = await unwrap(Promise.resolve(failure)).catch(value => value)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({code: 'conflict', status: 409, details: {version: 2}})
  })

  it('运行与插件请求保留专用超时和显式覆盖', async () => {
    for (const [send, timeout, path] of [
      [() => runtimeRequest('/transport-test'), 65000, '/api/v1/transport-test'],
      [() => pluginRequest('/transport-test'), 30000, '/api/v1/plugins/transport-test'],
      [() => runtimeRequest('/transport-test', 'GET', undefined, {timeout: 25}), 25, '/api/v1/transport-test'],
    ] as const) {
      awaitAbort()
      const pending = send().catch(error => error)
      await vi.advanceTimersByTimeAsync(timeout - 1)
      expect(fetchMock.mock.calls.at(-1)?.[1]?.signal?.aborted).toBe(false)
      await vi.advanceTimersByTimeAsync(1)
      expect(await pending).toMatchObject({code: 'timeout'})
      expect(fetchMock.mock.calls.at(-1)?.[0]).toBe(path)
      expect(vi.getTimerCount()).toBe(0)
    }
  })
})

 it('超时后的外部取消不能改写首次中止原因', async () => {
  let rejectFetch!: (error: Error) => void
  fetchMock.mockImplementation(() => new Promise((_resolve, reject) => { rejectFetch = reject }))
  const controller = new AbortController()
  const pending = request('/transport-test', {timeout: 10, signal: controller.signal})
  await vi.advanceTimersByTimeAsync(10)
  controller.abort()
  rejectFetch(new Error('aborted'))
  expect(await pending).toMatchObject({ok: false, code: 'timeout'})
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('retains transport resource identity and original detail for later language changes', async () => {
  const { setLocale, t } = await import('@/i18n')
  const { noticeText } = await import('../noticeText')
  try {
    setLocale('en')
    fetchMock.mockRejectedValueOnce(new Error('Original detail 原文'))
    const result = await request('/transport-test')
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.copy).toEqual({ key: 'ui.http.networkUnavailable' })
    expect(result.details).toEqual({cause: 'Original detail 原文'})
    for (const language of ['en', 'zh-CN', 'en'] as const) {
      setLocale(language)
      expect(noticeText(result.copy!)).toBe(t('ui.http.networkUnavailable'))
      expect(noticeText(result.copy!)).not.toContain('Original detail')
    }
    expect(fetchMock).toHaveBeenCalledTimes(1)
  } finally { setLocale('zh-CN') }
})


describe('请求概要协议拒绝', () => {
  it('对两种机器错误提供双语重启说明，保留诊断且不重放', async () => {
    for (const locale of ['en', 'zh-CN', 'en'] as const) {
      setLocale(locale)
      for (const code of ['bad-request', 'request-trace/unsupported-part']) {
        fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({code, error: 'SERVER ORIGINAL', details: {part: 'summary'}}), {status: 400}))
        const before = fetchMock.mock.calls.length
        const result = await api.requestTrace('owner', 'trace', undefined, true)
        expect(result).toMatchObject({ok: false, code, status: 400, copy: {key: 'ui.requestComposition.summaryRejected'}, details: {original: 'SERVER ORIGINAL', details: {part: 'summary'}}})
        if (!result.ok) expect(result.message).toContain(locale === 'en' ? 'restart' : '重启')
        expect(fetchMock).toHaveBeenCalledTimes(before + 1)
        expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('?part=summary')
      }
    }
  })

  it('普通详情、权限、缺失和服务器故障保持原始原因', async () => {
    for (const [summary, status, code] of [[false,400,'bad-request'], [true,403,'forbidden'], [true,404,'not-found'], [true,500,'internal']] as const) {
      fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({code, error: 'REAL FAILURE'}), {status}))
      expect(await api.requestTrace('owner','trace',undefined,summary)).toMatchObject({ok:false,status,code,message:'REAL FAILURE'})
    }
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })
})
