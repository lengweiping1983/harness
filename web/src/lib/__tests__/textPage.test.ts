import {afterEach,expect,it,vi} from 'vitest'
import {api} from '../api'
import {setLocale,t} from '@/i18n'
import {noticeText} from '../noticeText'

const readers = [
 ['request', (offset:number)=>api.requestTracePart('owner','trace','body',0,offset)],
 ['execution', (offset:number)=>api.executionTracePart('owner','entry','result',offset,'agent')],
 ['skill', (offset:number)=>api.skillOriginal('owner',7,20,offset,undefined,'agent')],
 ['compaction', (offset:number)=>api.compactionPart('owner',7,20,'summary',offset,undefined,'agent')],
] as const
afterEach(()=>{vi.unstubAllGlobals();setLocale('zh-CN')})
it.each(readers)('%s rejects contradictory successful page receipts without rereading',async(_name,read)=>{
 for(const page of [
  {text:'foreign',offset:1,total_chars:8,next_offset:null},
  {text:'😀甲',offset:0,total_chars:1,next_offset:null},
  {text:'abc',offset:0,total_chars:9,next_offset:0},
  {text:'abc',offset:0,total_chars:3,next_offset:3},
  {text:'abc',offset:0,total_chars:9,next_offset:null},
  {text:'',offset:0,total_chars:9,next_offset:0},
 ]){
  const fetch=vi.fn(async(_path:RequestInfo|URL)=>new Response(JSON.stringify(page),{status:200}));vi.stubGlobal('fetch',fetch)
  const result=await read(0)
  expect(result.ok).toBe(false)
  if(result.ok)throw Error('Invalid page accepted')
  expect(result.code).toBe('response/invalid-schema')
  for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(result.copy??'')).toBe(t('ui.http.invalidResponse'))}
  expect(fetch).toHaveBeenCalledTimes(1)
 }
})
it.each(readers)('%s preserves valid Unicode text, continuation and empty final pages',async(_name,read)=>{
 for(const page of [
  {text:'😀甲',offset:0,total_chars:5,next_offset:2},
  {text:'abc',offset:2,total_chars:5,next_offset:null},
  {text:'',offset:5,total_chars:5,next_offset:null},
 ]){
  const fetch=vi.fn(async(_path:RequestInfo|URL)=>new Response(JSON.stringify(page),{status:200}));vi.stubGlobal('fetch',fetch)
  const result=await read(page.offset)
  expect(result).toEqual({ok:true,data:page})
  const url=new URL(String(fetch.mock.calls[0][0]),'http://fixture.invalid')
  expect(url.searchParams.get('offset')).toBe(String(page.offset))
  expect(fetch).toHaveBeenCalledTimes(1)
 }
})
it.each(readers)('%s preserves original service rejection without a retry',async(_name,read)=>{
 const fetch=vi.fn(async(_path:RequestInfo|URL)=>new Response(JSON.stringify({code:'fixture/refused',message:'Original refusal 原文'}),{status:409}));vi.stubGlobal('fetch',fetch)
 expect(await read(0)).toMatchObject({ok:false,status:409,code:'fixture/refused',message:'Original refusal 原文'})
 expect(fetch).toHaveBeenCalledTimes(1)
})
