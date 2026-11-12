import {t} from '@/i18n'
import {request, type ApiResult} from './http'
import type {RequestTracePart} from './requestTraces'

/** 原文分页按 Unicode 码点核对请求范围，不修补服务端回执或重复读取。 */
export async function readTextPage(path: string, offset: number, signal?: AbortSignal, timeout = 20_000): Promise<ApiResult<RequestTracePart>> {
  const result = await request<RequestTracePart>(path, {signal, timeout})
  if (!result.ok) return result
  const page = result.data
  const end = page && typeof page.text === 'string' ? offset + Array.from(page.text).length : NaN
  if (Number.isSafeInteger(offset) && offset >= 0 && page?.offset === offset
      && Number.isSafeInteger(page.total_chars) && page.total_chars >= end && Number.isSafeInteger(end)
      && (end === page.total_chars || end > offset)
      && page.next_offset === (end < page.total_chars ? end : null)) return result
  return {ok: false, status: 200, code: 'response/invalid-schema', message: t('ui.http.invalidResponse'), copy: {key: 'ui.http.invalidResponse'}}
}
