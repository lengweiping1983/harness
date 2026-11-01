import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {RunHistoryCard} from '@/components/inspector/RunHistoryCard'
import {TurnElapsed} from '@/components/chat/blocks/TurnElapsed'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RunRecord } from '../protocol.generated'

vi.mock('../http', async importOriginal => {
  const actual = await importOriginal<typeof import('../http')>()
  return {...actual, runtimeRequest: vi.fn()}
})

import { ApiError, runtimeRequest } from '../http'
import { continueRun, runStatus } from '../runHistory'
import { setLocale } from '@/i18n'

const request = vi.mocked(runtimeRequest)
const resumed: RunRecord = {id: 'next', session_id: 'session', text: '继续',
  status: 'queued', created_at: 1, retry_of: 'previous'}

beforeEach(() => request.mockReset())
afterEach(() => setLocale('zh-CN'))

describe('恢复执行的未知提交结果', () => {
  it('回执已存在时直接复用，不创建第二次执行', async () => {
    request.mockResolvedValueOnce({run: resumed})
    await expect(continueRun('session', 'previous')).resolves.toEqual(resumed)
    expect(request.mock.calls.map(call => call[1] ?? 'GET')).toEqual(['GET'])
  })

  it('提交断线后只读回查原回执，不自动重发', async () => {
    request.mockResolvedValueOnce({run: null}).mockRejectedValueOnce(new Error('连接中断'))
      .mockResolvedValueOnce({run: resumed})
    await expect(continueRun('session', 'previous')).resolves.toEqual(resumed)
    expect(request.mock.calls.map(call => call[1] ?? 'GET')).toEqual(['GET', 'POST', 'GET'])
    expect(request.mock.calls[2]?.[0]).toBe(request.mock.calls[0]?.[0])
  })

  it('提交与回查都断线时明确失败，仍只发送一次', async () => {
    request.mockResolvedValueOnce({run: null}).mockRejectedValueOnce(new Error('提交断线'))
      .mockRejectedValueOnce(new Error('回查断线'))
    await expect(continueRun('session', 'previous')).rejects.toThrow()
    expect(request.mock.calls.filter(call => call[1] === 'POST')).toHaveLength(1)
  })

  it('回执属于另一会话时拒绝，不借用其他会话的执行', async () => {
    request.mockResolvedValueOnce({run: {...resumed, session_id: 'other'}})
    await expect(continueRun('session', 'previous')).rejects.toMatchObject({code: 'run/idempotency-conflict'})
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('明确拒绝不解释成网络未知，也不再次发送', async () => {
    const denied = new ApiError({ok: false, status: 403, code: 'access/denied', message: '无权限'})
    request.mockResolvedValueOnce({run: null}).mockRejectedValueOnce(denied)
    await expect(continueRun('session', 'previous')).rejects.toBe(denied)
    expect(request).toHaveBeenCalledTimes(2)
  })
})


describe('recovery receipts across language changes', () => {
  const draft = {mode: 'continue' as const, text: 'original draft', recovery_version: 'version', verification_note: 'checked'}
  for (const language of ['zh-CN', 'en'] as const) {
    it(`recognizes a ${language} receipt after switching language`, async () => {
      setLocale(language)
      const text = 'Opaque assembled prompt 原文 ' + language
      setLocale(language === 'en' ? 'zh-CN' : 'en')
      const run = {...resumed, text, recovery: {mode: 'continue' as const, source_version: 'version', verification_note: 'checked', instruction_text: draft.text}}
      request.mockResolvedValueOnce({run})
      await expect(continueRun('session', 'previous', draft)).resolves.toEqual(run)
      expect(request).toHaveBeenCalledTimes(1)
    })
  }
  it('rejects absent or conflicting structured instructions without parsing the prompt', async () => {
    for (const instruction_text of [undefined, 'Other instructions']) {
      request.mockResolvedValueOnce({run: {...resumed, text: draft.text,
        recovery: {mode: draft.mode, source_version: draft.recovery_version,
          verification_note: draft.verification_note, instruction_text}}})
      await expect(continueRun('session', 'previous', draft)).rejects.toMatchObject({code: 'run/idempotency-conflict'})
    }
    expect(request.mock.calls.map(call => call[1] ?? 'GET')).toEqual(['GET', 'GET'])
  })
  it('still rejects changed instructions and recovery evidence', async () => {
    setLocale('en')
    for (const changes of [{text: 'different draft'}, {recovery_version: 'different'}, {verification_note: 'different'}]) {
      request.mockResolvedValueOnce({run: {...resumed,
        text: 'Opaque assembled prompt 原文',
        recovery: {mode: 'continue', source_version: 'version', verification_note: 'checked', instruction_text: draft.text}}})
      await expect(continueRun('session', 'previous', {...draft, ...changes})).rejects.toMatchObject({code: 'run/idempotency-conflict'})
    }
    expect(request.mock.calls.some(call => call[1] === 'POST')).toBe(false)
  })
})

it('distinguishes stopped runs from restart interruption without merging removed or steered tasks', () => {
 for(const locale of ['en','zh-CN','en'] as const) {
  setLocale(locale)
  const base: RunRecord = {...resumed, status:'interrupted'}
  for(const record of [base,{...base,result:{reason:'aborted'}},{...base,error:{code:'run/cancelled',message:'Original diagnostic'}}]) {
   expect(runStatus(record)).toBe(locale==='en'?'Stopped':'已停止')
  }
  const interrupted={...base,error:{code:'run/restarted',message:'Original diagnostic'}}
  expect(runStatus(interrupted)).toBe(locale==='en'?'Interrupted':'已中断')
  expect(runStatus({...base,error:{code:'run/removed'}})).not.toBe(runStatus(base))
  expect(runStatus({...base,error:{code:'run/steered'}})).not.toBe(runStatus(base))
  expect(runStatus({...base,status:'running'},{stopping:true,waiting:null})).toBe(locale==='en'?'Stopping':'正在停止')
  expect(interrupted.error.message).toBe('Original diagnostic')
 }
})

it('renders the same stopped and interrupted state in the real history card and turn timer', () => {
 for(const locale of ['en','zh-CN','en'] as const) {
  setLocale(locale)
  for(const interrupted of [false,true]) {
   const label=locale==='en'?(interrupted?'Interrupted':'Stopped'):(interrupted?'已中断':'已停止')
   const record: RunRecord={...resumed,status:'interrupted',started_at:100,finished_at:240,
    error:interrupted?{code:'run/restarted',message:'Original diagnostic'}:null}
   const card=renderToStaticMarkup(createElement(RunHistoryCard,{record,sessionId:'session',busy:false,onContinue:()=>{},onOriginal:()=>{}}))
   const timer=renderToStaticMarkup(createElement(TurnElapsed,{activity:{turn:1,startedAt:100000,endedAt:240000,endSeq:20,reason:interrupted?'interrupted':'aborted'},active:false}))
   const duration=locale==='en'?'2 min 20 sec':'2 分钟 20 秒'
   expect(card).toContain(label)
   expect(timer).toContain(label)
   expect(card).toContain(duration)
   expect(timer).toContain(duration)
  }
 }
})
