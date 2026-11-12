import {afterEach, expect, it, vi} from 'vitest'
import type {RecoveryPreview} from '../protocol.generated'
vi.mock('../http', async original => ({...await original<typeof import('../http')>(), runtimeRequest: vi.fn()}))
import {runtimeRequest} from '../http'
import {readRecoveryPreview} from '../runHistory'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {setLocale, t} from '@/i18n'
const request = vi.mocked(runtimeRequest)
const tool = {call_id:'call',name:'read',effect:'read',idempotent:true,status:'reported_success' as const,source_seq:1,arguments:'{}',result:'Original receipt 原文',result_truncated:false}
const first: RecoveryPreview = {session_id:'owner',run_id:'run',status:'failed',turn:1,task:'Original task 原文',task_truncated:false,error:'Original error 原文',scope_known:true,version:'version',tools:[tool],total_tools:2,uncertain_operations:0,verification_required:false,next_offset:1,inspection_text:'Inspect 原文',continuation_text:'Continue 原文',known_continuations:{}}
const last = {...first, tools:[{...tool,source_seq:2}], next_offset:null}
afterEach(()=>{vi.resetAllMocks();setLocale('zh-CN')})
it.each([
  ['initial foreign session', {...first,session_id:'other'}, undefined],
  ['initial foreign run', {...first,run_id:'other'}, undefined],
  ['initial empty nonfinal', {...first,tools:[]}, undefined],
  ['initial invalid cursor', {...first,next_offset:0}, undefined],
  ['later foreign session', {...last,session_id:'other'}, first],
  ['later foreign run', {...last,run_id:'other'}, first],
  ['later changed total', {...last,total_tools:3}, first],
  ['later empty page', {...last,tools:[]}, first],
  ['later negative total', {...last,total_tools:-1}, first],
  ['later repeated cursor', {...last,next_offset:1}, first],
  ['negative pending count', {...first,uncertain_operations:-1}, undefined],
  ['fractional pending count', {...first,uncertain_operations:0.5}, undefined],
  ['unsafe pending count', {...first,uncertain_operations:Number.MAX_SAFE_INTEGER+1}, undefined],
  ['pending count exceeds total', {...first,uncertain_operations:3,verification_required:true}, undefined],
  ['unknown scope without verification', {...first,scope_known:false}, undefined],
  ['pending operations without verification', {...first,uncertain_operations:1}, undefined],
  ['unnecessary verification flag', {...first,verification_required:true}, undefined],
  ['same version changed pending count', {...last,uncertain_operations:1,verification_required:true}, first],
  ['same version changed scope', {...last,scope_known:false,verification_required:true}, first],
  ['page repeats earlier evidence', {...last,tools:[tool]}, first],
  ['page repeats evidence internally', {...first,tools:[tool,tool],next_offset:null}, undefined],
  ['count omits visible unknown write', {...first,tools:[{...tool,effect:'external_write',status:'unknown'}]}, undefined],
  ['complete list disagrees with pending count', {...last,total_tools:1,uncertain_operations:1,verification_required:true}, undefined],
  ['same version removes pending requirement', last, {...first,uncertain_operations:1,verification_required:true}],
] as const)('%s rejects without another request and keeps an error resource',async(_label,page,previous)=>{
  request.mockResolvedValue(page)
  const error = await readRecoveryPreview('owner','run',new AbortController().signal,previous).catch(error=>error)
  expect(error).toBeInstanceOf(Error)
  for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(failureText(error))).toBe(t('ui.http.invalidResponse'))}
  expect(request).toHaveBeenCalledTimes(1)
})
it('accepts initial, final, and empty completed lists while forwarding cancellation',async()=>{
 const controller=new AbortController()
 request.mockResolvedValueOnce(first).mockResolvedValueOnce(last).mockResolvedValueOnce({...first,tools:[],total_tools:0,next_offset:null})
 expect(await readRecoveryPreview('owner','run',controller.signal)).toBe(first)
 expect(await readRecoveryPreview('owner','run',controller.signal,first)).toBe(last)
 expect(await readRecoveryPreview('owner','run',controller.signal)).toMatchObject({total_tools:0,next_offset:null})
 expect(request.mock.calls[1]).toEqual(['/sessions/owner/runs/run/recovery?offset=1','GET',undefined,{signal:controller.signal}])
})
it('keeps the explicit changed-version error',async()=>{
 request.mockResolvedValue({...last,version:'changed'})
 const error=await readRecoveryPreview('owner','run',new AbortController().signal,first).catch(error=>error)
 expect(failureText(error)).toEqual({key:'ui.components.inspector.RecoveryReview.ad2c90cbaebc'})
 expect(request).toHaveBeenCalledTimes(1)
})

it('retains full-run pending counts while accepting later writes and repeated call ids with distinct sequence identity',async()=>{
 const prefix={...first,uncertain_operations:1,verification_required:true}
 const suffix={...prefix,tools:[{...tool,source_seq:2,effect:'external_write',status:'reported_error' as const}],next_offset:null}
 request.mockResolvedValueOnce(prefix).mockResolvedValueOnce(suffix)
 expect(await readRecoveryPreview('owner','run',new AbortController().signal)).toBe(prefix)
 expect(await readRecoveryPreview('owner','run',new AbortController().signal,prefix)).toBe(suffix)
})
it('unknown scope remains verification-required even with no tool records',async()=>{
 const unknown={...first,scope_known:false,turn:null,tools:[],total_tools:0,verification_required:true,next_offset:null}
 request.mockResolvedValue(unknown)
 expect(await readRecoveryPreview('owner','run',new AbortController().signal)).toBe(unknown)
})
