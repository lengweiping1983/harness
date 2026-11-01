import {afterEach, describe, expect, it, vi} from 'vitest'
vi.mock('../http', async original => ({...await original<typeof import('../http')>(), runtimeRequest: vi.fn()}))
import {runtimeRequest} from '../http'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {setLocale, t} from '@/i18n'
import {changeQueueControl, readQueueControl, QueueControlUnknown} from '../queueControl'
import {verifyQueuedInput, verifyWithdrawnInput, steerQueuedInput, QueueSaveUnknown, QueueWithdrawalUnknown, type QueueEntry} from '../queueEdits'

const request = vi.mocked(runtimeRequest)
const entry = {id:'entry',kind:'runs',text:'Original input 原文',edit_version:'a'.repeat(64)} as QueueEntry
const thrown = async (action: () => Promise<unknown>) => {
  try {await action()} catch (failure) {return failure}
  throw Error('Expected operation to fail')
}
afterEach(() => {request.mockReset();setLocale('zh-CN')})

function verifyLanguage(failure: unknown, key: string) {
  const copy = failureText(failure)
  const calls = request.mock.calls.length
  for (const locale of ['en','zh-CN','en'] as const) {
    setLocale(locale)
    expect(noticeText(copy)).toBe(t(key))
    expect(request).toHaveBeenCalledTimes(calls)
  }
}

describe('queue errors retain resources without repeating operations', () => {
  it('keeps an unknown control write and checks only its resulting state', async () => {
    setLocale('en')
    request.mockRejectedValue(new Error('lost response'))
    const failure = await thrown(() => changeQueueControl('owner',true))
    expect(failure).toBeInstanceOf(QueueControlUnknown)
    expect(request.mock.calls.map(call=>call[1])).toEqual(['PATCH',undefined])
    verifyLanguage(failure,'ui.lib.queueControl.96befea94f27')
  })
  it('rejects a control state owned by another conversation', async () => {
    request.mockResolvedValue({session_id:'other',paused:true,running:false,pending:0,pause_reason:'user',updated_at:1})
    verifyLanguage(await thrown(()=>readQueueControl('owner')),'ui.lib.queueControl.b46b7c15807a')
    expect(request).toHaveBeenCalledTimes(1)
  })
  it('keeps save verification read-only and preserves the unknown error type', async () => {
    request.mockRejectedValue(new Error('unavailable'))
    const failure = await thrown(()=>verifyQueuedInput('owner',entry,'edited'))
    expect(failure).toBeInstanceOf(QueueSaveUnknown)
    verifyLanguage(failure,'ui.lib.queueEdits.ca71bc9168b4')
    expect(request.mock.calls[0][1]).toBeUndefined()
  })
  it('keeps withdrawal verification unknown when its read fails', async () => {
    request.mockRejectedValue(new Error('unavailable'))
    const failure = await thrown(()=>verifyWithdrawnInput('owner',entry))
    expect(failure).toBeInstanceOf(QueueWithdrawalUnknown)
    verifyLanguage(failure,'ui.lib.queueEdits.withdrawUnknown')
    expect(request).toHaveBeenCalledTimes(1)
  })
  for (const [status,text,key] of [['queued','changed','withdrawChanged'],['running',entry.text,'withdrawStarted']]) {
    it(`retains the ${status} withdrawal conflict code and resource`, async () => {
      request.mockResolvedValue({id:entry.id,session_id:'owner',text,status})
      const failure = await thrown(()=>verifyWithdrawnInput('owner',entry))
      expect(failure).toMatchObject({status:409,code:status==='queued'?'run/queue-changed':'run/not-queued'})
      verifyLanguage(failure,'ui.lib.queueEdits.'+key)
    })
  }
  it('checks a lost steer receipt without reposting the original transfer', async () => {
    request.mockRejectedValue(new Error('unavailable'))
    const failure = await thrown(()=>steerQueuedInput('owner',entry,3))
    verifyLanguage(failure,'ui.lib.queueEdits.25bb4127c0b6')
    expect(request.mock.calls.map(call=>call[1])).toEqual(['POST',undefined])
  })
  it('preserves unrecognized external errors even when text equals a product translation', () => {
    setLocale('en')
    const raw = t('ui.lib.queueEdits.withdrawUnknown')
    const saved = failureText(new Error(raw))
    setLocale('zh-CN')
    expect(noticeText(saved)).toBe(raw)
  })
})
