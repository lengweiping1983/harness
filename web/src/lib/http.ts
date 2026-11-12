import type { ResourceCopy, ResourceTemplate } from '@/types'
import { t } from '@/i18n'
import {collaborationErrorCopy} from './collaborationError'
import {responseIssue} from './responseValidation'

export interface ApiFailure {
  ok: false
  status: number
  code: string
  message: string
  details?: unknown
  copy?: ResourceCopy | ResourceTemplate
}
export type ApiResult<T> = { ok: true; data: T } | ApiFailure
export type RequestOptions = RequestInit & { timeout?: number }

/** 失败回执只有明确的4xx拒绝（408除外）才能排除写入；业务持久化拒绝由调用方另行判断。 */
export function isUnconfirmedWrite(failure: ApiFailure): boolean {
  return !(failure.status >= 400 && failure.status < 500 && failure.status !== 408)
}

export class ApiError extends Error {
  constructor(readonly failure: ApiFailure) { super(failure.message); this.name = 'ApiError' }
  get code(): string { return this.failure.code }
  get status(): number { return this.failure.status }
  get details(): unknown { return this.failure.details }
}

/** 按错误码与资源键验证自有诊断身份，用户字段只作为原文参数呈现。 */
function requestErrorCopy(code: string, details: unknown): ResourceCopy | undefined {
  const rules: Record<string, Record<string, Record<string, 'string' | 'number'>>> = {
    'bad-json': {'ui.http.invalidJsonRequest': {}},
    'session/invalid-field': {'ui.session.titleTooLong': {limit: 'number'}, 'ui.session.unknownField': {field: 'string'}},
    'voice/invalid': {'ui.voice.invalidPreparation': {}},
  }
  if (!Object.hasOwn(rules, code) || !details || typeof details !== 'object') return
  const copy = (details as Record<string, unknown>).copy
  if (!copy || typeof copy !== 'object') return
  const {key, params = {}} = copy as Record<string, unknown>
  if (typeof key !== 'string' || !Object.hasOwn(rules[code], key) || !params || typeof params !== 'object' || Array.isArray(params)) return
  const expected = rules[code][key], entries = Object.entries(params)
  if (entries.length !== Object.keys(expected).length || entries.some(([name, value]) => !Object.hasOwn(expected, name)
    || typeof value !== expected[name] || typeof value === 'number' && !Number.isFinite(value))) return
  return {key, params: params as Record<string, string | number>}
}

/** One transport owns cancellation, response parsing and timeout cleanup. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  const { timeout = 20_000, signal, ...init } = options
  const controller = new AbortController()
  let abortReason: 'cancelled' | 'timeout' | undefined
  const abort = (reason: 'cancelled' | 'timeout') => {
    if (controller.signal.aborted) return
    abortReason = reason
    controller.abort()
  }
  const cancel = () => abort('cancelled')
  if (signal?.aborted) cancel()
  signal?.addEventListener('abort', cancel, { once: true })
  const timer = setTimeout(() => abort('timeout'), timeout)
  try {
    const headers = new Headers(init.headers)
    if (!headers.has('Accept')) headers.set('Accept', 'application/json')
    if (init.body && !headers.has('Content-Type') && typeof init.body === 'string') headers.set('Content-Type', 'application/json')
    const response = await fetch(path, { ...init, headers, signal: controller.signal })
    const text = await response.text()
    let payload: unknown = undefined
    if (text.trim()) {
      try { payload = JSON.parse(text) }
      catch {
        if (response.ok) return { ok: false, status: response.status, code: 'response/invalid-json', message: t('ui.http.invalidResponse'), copy: { key: 'ui.http.invalidResponse' } }
      }
    }
    if (!response.ok) {
      const body = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {}
      const code = typeof body.code === 'string' ? body.code : 'http/' + response.status
      const original = typeof body.message === 'string' && body.message || typeof body.error === 'string' && body.error || ''
      const copy = collaborationErrorCopy(code) ?? requestErrorCopy(code, body.details)
        ?? (!original ? { key: 'ui.http.failure', params: { status: response.status } } : undefined)
      return { ok: false, status: response.status, code, message: copy ? t(copy.key, copy.params) : original, details: body.details, ...(copy ? { copy } : {}) }
    }
    const issue = responseIssue(path, init.method ?? 'GET', response.status, payload)
    if (issue) return {ok: false, status: response.status, code: 'response/invalid-schema',
      message: t('ui.http.invalidResponse'), copy: { key: 'ui.http.invalidResponse' }, details: {path: issue}}
    return { ok: true, data: payload as T }
  } catch (error) {
    const aborted = controller.signal.aborted
    const copy: ResourceCopy = aborted
      ? { key: abortReason === 'cancelled' ? 'ui.lib.api.282c4cfbc8fd' : 'ui.lib.api.8b7f52f0dbb6' }
      : { key: 'ui.http.networkUnavailable' }
    return { ok: false, status: 0, code: aborted ? abortReason ?? 'cancelled' : 'network',
      message: t(copy.key, copy.params), copy,
      ...(!aborted ? {details: {cause: error instanceof Error ? error.message : String(error)}} : {}) }
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', cancel)
  }
}

/** 仅供显式目录 GET 使用；重试共享总截止时间，取消与协议错误不重试。 */
export async function readRequest<T>(path: string, options: Omit<RequestOptions, 'method' | 'body'> = {}): Promise<ApiResult<T>> {
  const deadline = Date.now() + (options.timeout ?? 20_000)
  const delays = [250, 750, 1500, 3000]
  for (let attempt = 0; ; attempt++) {
    const result = await request<T>(path, {...options, method: 'GET', timeout: Math.max(1, deadline - Date.now())})
    if (result.ok || options.signal?.aborted || attempt >= delays.length
      || !(result.status === 0 && result.code === 'network' || [408, 500, 502, 503, 504].includes(result.status))
      || Date.now() + delays[attempt] >= deadline) return result
    await new Promise<void>(resolve => {
      const finish = () => {clearTimeout(timer); options.signal?.removeEventListener('abort', finish); resolve()}
      const timer = setTimeout(finish, delays[attempt])
      options.signal?.addEventListener('abort', finish, {once: true})
      if (options.signal?.aborted) finish()
    })
    if (options.signal?.aborted) {
      const copy: ResourceCopy = {key: 'ui.lib.api.282c4cfbc8fd'}
      return {ok: false, status: 0, code: 'cancelled', message: t(copy.key), copy}
    }
  }
}

export async function unwrap<T>(pending: Promise<ApiResult<T>>): Promise<T> {
  const result = await pending
  if (!result.ok) throw new ApiError(result)
  return result.data
}

function jsonRequest<T>(prefix: string, timeout: number, path: string, method = 'GET', body?: unknown, options: RequestOptions = {}): Promise<T> {
  return unwrap(request<T>(prefix + path, { timeout, ...options, method, body: body === undefined ? undefined : JSON.stringify(body) }))
}

export function runtimeRequest<T>(path: string, method = 'GET', body?: unknown, options: RequestOptions = {}): Promise<T> {
  return jsonRequest<T>('/api/v1', 65_000, path, method, body, options)
}

export function pluginRequest<T>(path: string, method = 'GET', body?: unknown, options: RequestOptions = {}): Promise<T> {
  return jsonRequest<T>('/api/v1/plugins', 30_000, path, method, body, options)
}
