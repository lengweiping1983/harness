import {beforeEach, describe, expect, it, vi} from 'vitest'
import {setLocale, t} from '@/i18n'
import {ApiError} from '../http'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {authoringText, submitAuthoring} from '../agentAuthoring'
const request = vi.hoisted(() => vi.fn())
vi.mock('../http', async importOriginal => ({...await importOriginal<typeof import('../http')>(), runtimeRequest: request}))
const submission = {id: 'original-request', session: 'original-session', text: 'Original 原文'}
const run = {session_id: submission.session, text: submission.text, id: 'run'}
beforeEach(() => {request.mockReset(); setLocale('en')})
describe('authoring receipt and language ownership', () => {
  it('keeps creator names and user requirements while translating instructions', () => {
    for (const locale of ['en', 'zh-CN'] as const) {
      setLocale(locale)
      for (const kind of ['skill', 'plugin'] as const) {
        expect(authoringText(kind, '  User 原文  ')).toBe(`/skill ${kind}-creator ${t('ui.authoring.task.' + kind)}\n\n${t('ui.authoring.requirements')}\nUser 原文`)
      }
    }
  })
  it.each([0, 200, 408, 500])('reads the original receipt once after uncertain status %s', async status => {
    request.mockRejectedValueOnce(new ApiError({ok:false, status, code:'response/invalid-json', message:'diagnostic', copy:{key:'ui.http.invalidResponse'}})).mockResolvedValueOnce({run})
    expect(await submitAuthoring(submission)).toEqual(run)
    expect(request.mock.calls.map(call=>call[1] ?? 'GET')).toEqual(['POST','GET'])
    expect(request.mock.calls[0][3].headers['Idempotency-Key']).toBe(submission.id)
    expect(request.mock.calls[1][0]).toBe('/sessions/original-session/submissions/original-request')
  })
  it('keeps an unresolved receipt resource across languages and never replays in read-only mode', async () => {
    request.mockResolvedValueOnce({run:{...run,session_id:'other'}})
    let saved: unknown
    try {await submitAuthoring(submission, true)} catch (error) {saved=failureText(error)}
    expect(request.mock.calls.map(call=>call[1] ?? 'GET')).toEqual(['GET'])
    for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(saved as ReturnType<typeof failureText>)).toBe(t('ui.authoring.unknown'))}
  })
  it('does not read or obscure an explicit rejection', async () => {
    const error=new ApiError({ok:false,status:409,code:'run/conflict',message:'External 原文'})
    request.mockRejectedValueOnce(error)
    await expect(submitAuthoring(submission)).rejects.toBe(error)
    expect(request).toHaveBeenCalledTimes(1)
  })
})
