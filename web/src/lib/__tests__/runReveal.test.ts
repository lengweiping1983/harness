import {afterEach, beforeEach, expect, test, vi} from 'vitest'
vi.mock('../http', async original => ({...await original<typeof import('../http')>(), runtimeRequest: vi.fn()}))
import {ApiError, runtimeRequest} from '../http'
import {readRunTarget} from '../runReveal'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {setLocale, t} from '@/i18n'

const request = vi.mocked(runtimeRequest)
const record = {id: 'run/原文', session_id: 'owner/原文', status: 'failed', text: 'Original task', created_at: 1}
beforeEach(() => {request.mockReset(); setLocale('en')})
afterEach(() => setLocale('zh-CN'))
function language(error: unknown, key: string) {
  const copy = failureText(error), calls = request.mock.calls.length
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale); expect(noticeText(copy)).toBe(t(key)); expect(request).toHaveBeenCalledTimes(calls)
  }
}
test.each([0, -1, NaN, Infinity, 1.5, Number.MAX_SAFE_INTEGER + 1])('invalid turn %s is rejected before reading with retained resource identity', async turn => {
  const error = await readRunTarget({sessionId: record.session_id, turn, seq: 1}).catch(error => error)
  expect(error).toMatchObject({status: 400, code: 'run/invalid-target'})
  language(error, 'ui.lib.runReveal.3b7cb0f78970'); expect(request).not.toHaveBeenCalled()
})
test.each(['run', 'turn'])('%s target reads a precise encoded path with the caller signal', async mode => {
  const signal = new AbortController().signal
  request.mockResolvedValue(record)
  expect(await readRunTarget({sessionId: record.session_id, ...(mode === 'run' ? {runId: record.id} : {turn: 2}), seq: 1}, signal)).toBe(record)
  expect(request).toHaveBeenCalledWith('/sessions/owner%2F%E5%8E%9F%E6%96%87/' + (mode === 'run' ? 'runs/run%2F%E5%8E%9F%E6%96%87' : 'turns/2/run'), 'GET', undefined, {signal})
})
test.each([{...record, session_id: 'other'}, {...record, id: 'other'}, {...record, id: ''}])('mismatched run %j cannot be presented as the target', async value => {
  request.mockResolvedValue(value)
  const error = await readRunTarget({sessionId: record.session_id, runId: record.id, seq: 1}).catch(error => error)
  expect(error).toMatchObject({status: 409, code: 'run/invalid-target'})
  language(error, 'ui.lib.runReveal.1b916fec6f48'); expect(request).toHaveBeenCalledTimes(1)
})
test('unavailable and third-party failures preserve their original identities without another request', async () => {
  const original = new ApiError({ok: false, status: 404, code: 'external/error', message: 'Original 原文 {diagnostic}'})
  request.mockRejectedValue(original)
  expect(await readRunTarget({sessionId: record.session_id, turn: 2, seq: 1}).catch(error => error)).toBe(original)
  for (const locale of ['en', 'zh-CN', 'en'] as const) {setLocale(locale); expect(noticeText(failureText(original))).toBe(original.message)}
  expect(request).toHaveBeenCalledTimes(1)
})
