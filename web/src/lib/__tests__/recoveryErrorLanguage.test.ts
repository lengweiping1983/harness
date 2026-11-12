import {afterEach,beforeEach,expect,test,vi} from 'vitest'
vi.mock('../http',async original=>({...await original<typeof import('../http')>(),runtimeRequest:vi.fn()}))
import {runtimeRequest} from '../http'
import {continueRun} from '../runHistory'
import {setLocale,t} from '@/i18n'
import {noticeText} from '../noticeText'
import {failureText} from '../resourceError'
const request=vi.mocked(runtimeRequest)
beforeEach(()=>setLocale('en'))
afterEach(()=>{request.mockReset();setLocale('zh-CN')})
function language(error:unknown,key:string){const copy=failureText(error),calls=request.mock.calls.length;for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t(key));expect(request).toHaveBeenCalledTimes(calls)}}
test('unconfirmed continuation keeps its resource after one POST and receipt lookup',async()=>{
 request.mockResolvedValueOnce({run:null}).mockRejectedValueOnce(new Error('lost write')).mockRejectedValueOnce(new Error('lost read'))
 const error=await continueRun('owner','original').catch(error=>error)
 language(error,'ui.lib.runHistory.ee75b48acc01')
 expect(request.mock.calls.map(call=>call[1]??'GET')).toEqual(['GET','POST','GET'])
 expect(request.mock.calls[0][0]).toBe(request.mock.calls[2][0])
})
test('foreign continuation identity retains the conflict code and resource without posting',async()=>{
 request.mockResolvedValueOnce({run:{session_id:'other',retry_of:'original'}})
 const error=await continueRun('owner','original').catch(error=>error)
 expect(error).toMatchObject({code:'run/idempotency-conflict',status:409})
 language(error,'ui.lib.runHistory.9836a9ec6624');expect(request).toHaveBeenCalledTimes(1)
})
test('changed recovery instructions retain their own resource and never reuse the receipt',async()=>{
 request.mockResolvedValueOnce({run:{session_id:'owner',retry_of:'original',recovery:{mode:'inspect',source_version:'different',verification_note:''},text:'Original text'}})
 const error=await continueRun('owner','original',{mode:'inspect',text:'Draft 原文',recovery_version:'current',verification_note:''}).catch(error=>error)
 expect(error).toMatchObject({code:'run/idempotency-conflict',status:409})
 language(error,'ui.lib.runHistory.052b2e1a4969');expect(request).toHaveBeenCalledTimes(1)
})
