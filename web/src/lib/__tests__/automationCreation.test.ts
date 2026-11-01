import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {saveAutomationOnce} from '../automationCreation'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {setLocale,t} from '@/i18n'
const body={name:'Original 原文',prompt:'Original prompt 原文',session_id:'fixture',mode:'session',schedule:{kind:'interval',seconds:60},timezone:'UTC',enabled:true}
let values: Map<string,string>
const key='harness:automation-creation:fixture'
const saved=(submitted:Record<string,unknown>)=>({...submitted,id:'auto_'+submitted.creation_id,enabled:Number(submitted.enabled),due:1800000000,running:0,failures:0,last_error:null})
const catalog=(rows:unknown[])=>({automations:rows,runs:[],scheduler:{running:true,owner:true,last_error:null,max_concurrent:1}})
beforeEach(()=>{
 values=new Map()
 vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value),removeItem:(key:string)=>values.delete(key)})
 vi.stubGlobal('navigator',{})
})
afterEach(()=>{vi.unstubAllGlobals();setLocale('zh-CN')})
it('persists identity before a single accepted POST and clears only a matching receipt',async()=>{
 const fetch=vi.fn(async(_path:RequestInfo|URL,init?:RequestInit)=>{
  expect(values.has(key)).toBe(true)
  const submitted=JSON.parse(init!.body as string)
  expect(submitted.creation_id).toMatch(/^[a-f0-9]{32}$/)
  return Response.json(saved(submitted))
 });vi.stubGlobal('fetch',fetch)
 await saveAutomationOnce(body)
 expect(fetch).toHaveBeenCalledTimes(1);expect(values.size).toBe(0)
})
it('lost response reads the original identity once without replaying creation',async()=>{
 let record!:Record<string,unknown>
 const fetch=vi.fn(async(_path:RequestInfo|URL,init?:RequestInit)=>{
  if(init?.method==='POST'){record=JSON.parse(init.body as string);throw new TypeError('Original network failure')}
  return Response.json(catalog([saved(record!)]))
 });vi.stubGlobal('fetch',fetch)
 await saveAutomationOnce(body)
 expect(fetch).toHaveBeenCalledTimes(2);expect(values.size).toBe(0)
 expect(fetch.mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(1)
})
it.each(['missing','foreign','duplicate','changed','malformed'])('%s readback retains unknown identity across callers and language changes',async kind=>{
 let record!:Record<string,unknown>
 const fetch=vi.fn(async(_path:RequestInfo|URL,init?:RequestInit)=>{
  if(init?.method==='POST'){record=JSON.parse(init.body as string);return new Response('broken')}
  const row=saved(record!)
  return Response.json(kind==='malformed'?{automations:{}}:catalog(kind==='missing'?[]:kind==='foreign'?[{...row,id:'other'}]:kind==='duplicate'?[row,row]:[{...row,prompt:'Other'}]))
 });vi.stubGlobal('fetch',fetch)
 const error=await saveAutomationOnce(body).catch(error=>error)
 expect(failureText(error)).toEqual({key:'ui.automations.creationUnconfirmed'})
 expect(values.has(key)).toBe(true)
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(failureText(error))).toBe(t('ui.automations.creationUnconfirmed'))}
 await expect(saveAutomationOnce(body)).rejects.toBeTruthy()
 expect(fetch.mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(1)
})
it('a reopened page resolves the saved identity before accepting a different draft',async()=>{
 const prior={...body,creation_id:'a'.repeat(32)};values.set(key,JSON.stringify(prior))
 const fetch=vi.fn(async()=>Response.json(catalog([saved(prior)])));vi.stubGlobal('fetch',fetch)
 const error=await saveAutomationOnce({...body,name:'New draft 原文'}).catch(error=>error)
 expect(failureText(error)).toEqual({key:'ui.automations.creationRecovered'})
 expect(fetch).toHaveBeenCalledTimes(1);expect(values.size).toBe(0)
 expect(fetch.mock.calls[0]).toBeTruthy()
})
it('an in-flight creator blocks a new page instance and keeps its own receipt',async()=>{
 let finish!:(response:Response)=>void
 const fetch=vi.fn(async()=>new Promise<Response>(resolve=>{finish=resolve}));vi.stubGlobal('fetch',fetch)
 const first=saveAutomationOnce(body)
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1))
 const submitted=JSON.parse(values.get(key)!)
 await expect(saveAutomationOnce({...body,name:'Other'})).rejects.toBeTruthy()
 expect(fetch).toHaveBeenCalledTimes(1)
 finish(Response.json(saved(submitted)));await first
 expect(values.size).toBe(0)
})
it('storage failure prevents POST, while corrupt pending bytes remain untouched',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch)
 values.set(key,'Original invalid bytes')
 await expect(saveAutomationOnce(body)).rejects.toBeTruthy()
 expect(values.get(key)).toBe('Original invalid bytes');expect(fetch).not.toHaveBeenCalled()
 values.clear();vi.stubGlobal('localStorage',{getItem:()=>null,setItem:()=>{throw new Error('Storage denied')}})
 const error=await saveAutomationOnce(body).catch(error=>error)
 expect(failureText(error)).toEqual({key:'ui.automations.creationStorageUnavailable'})
 expect(fetch).not.toHaveBeenCalled()
})
it('an explicit refusal clears the creation receipt without readback or retry',async()=>{
 const fetch=vi.fn(async()=>Response.json({message:'Original refusal 原文'},{status:409}));vi.stubGlobal('fetch',fetch)
 await expect(saveAutomationOnce(body)).rejects.toBeTruthy()
 expect(fetch).toHaveBeenCalledTimes(1);expect(values.size).toBe(0)
})
