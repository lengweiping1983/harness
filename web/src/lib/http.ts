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
      const copy = collaborationErrorCopy(code) ?? (!original ? { key: 'ui.http.failure', params: { status: response.status } } : undefined)
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
      : { key: 'ui.lib.api.4a5f658ed9ae', params: { detail: error instanceof Error ? error.message : String(error) } }
    return { ok: false, status: 0, code: aborted ? abortReason ?? 'cancelled' : 'network',
      message: t(copy.key, copy.params), copy }
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', cancel)
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
