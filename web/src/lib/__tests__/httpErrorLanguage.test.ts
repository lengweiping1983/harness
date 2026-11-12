import {afterEach, describe, expect, it, vi} from 'vitest'
import en from '@/i18n/locales/en.json'
import {setLocale,t} from '@/i18n'
import {request,unwrap,ApiError} from '../http'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'

const codes=Object.keys(en).filter(key=>key.startsWith('ui.collaboration.error.')).map(key=>key.slice('ui.collaboration.error.'.length))
afterEach(()=>{vi.unstubAllGlobals();setLocale('zh-CN')})
function rendering(copy:Parameters<typeof noticeText>[0],key:string,params:Record<string,string|number>){
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t(key,params))}
}
describe('HTTP error resources survive the transport boundary',()=>{
 it.each(codes)('preserves product code %s, status, details and resource through unwrap',async code=>{
  setLocale('en')
  const fetch=vi.fn().mockImplementation(()=>Promise.resolve(new Response(JSON.stringify({code,error:'宿主发生时的语言',details:{request_id:'original'}}),{status:403})))
  vi.stubGlobal('fetch',fetch)
  const failure=await unwrap(request('/transport-test',{method:'POST'})).catch(error=>error)
  expect(failure).toBeInstanceOf(ApiError)
  expect(failure).toMatchObject({code,status:403,details:{request_id:'original'}})
  rendering(failureText(failure),'ui.collaboration.error.'+code,{code})
  expect(fetch).toHaveBeenCalledTimes(1)
 })
 it.each([{code:'external/error'},{code:'external/error',message:{invalid:true},error:42},{code:'http/503',message:''}])('keeps the generic HTTP resource when no string body is available: %j',async body=>{
  const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify(body),{status:503}))
  vi.stubGlobal('fetch',fetch)
  const result=await request('/transport-test')
  if(result.ok)throw Error('Expected rejection')
  expect(result.code).toBe(body.code)
  expect(result.status).toBe(503)
  expect(result.copy).toBeDefined()
  rendering(result.copy!,'ui.http.failure',{status:503})
  expect(fetch).toHaveBeenCalledTimes(1)
 })
 it.each(['Original 原文','You do not have permission to perform this action. (access/denied)'])('preserves an unknown external code and its original text: %s',async original=>{
  setLocale('en')
  const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify({code:'third-party/error',error:original}),{status:409}))
  vi.stubGlobal('fetch',fetch)
  const result=await request('/transport-test')
  if(result.ok)throw Error('Expected rejection')
  expect(result.copy).toBeUndefined()
  const saved=failureText(new ApiError(result))
  for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(saved)).toBe(original)}
  expect(fetch).toHaveBeenCalledTimes(1)
 })
})
