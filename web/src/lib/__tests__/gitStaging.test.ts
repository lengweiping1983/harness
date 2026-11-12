import {afterEach,expect,it,vi} from 'vitest'
import {readGitPatch,writeGitOnce} from '../gitStaging'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {setLocale,t} from '@/i18n'
const head='a'.repeat(40)
const repo={root:'/fixture',branch:'main',refs:['main'],head,index_revision:'revision',conflicts:false,files:[],staged_files:[]}
afterEach(()=>{vi.unstubAllGlobals();setLocale('zh-CN')})
it('shares one accepted commit across callers until readback finishes and never replays',async()=>{
 let release!:()=>void
 const held=new Promise<void>(resolve=>{release=resolve})
 const fetch=vi.fn(async(_path:RequestInfo|URL,init?:RequestInit)=>{
  if(init?.method==='POST')return new Response('broken',{status:201})
  await held;return Response.json(repo)
 });vi.stubGlobal('fetch',fetch)
 const first=writeGitOnce('/sessions/one/git','/fixture','commit',{message:'Original',expected_revision:'revision'})
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2))
 const second=writeGitOnce('/sessions/two/git','/fixture','stage',{paths:['new'],expected_revision:'other'})
 expect(first.started).toBe(true);expect(second.started).toBe(false);expect(second.result).toBe(first.result)
 release();const result=await first.result
 expect(result.confirmed).toBe(false);expect(result.repository).toEqual(repo)
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(result.error!)).toContain(t('ui.http.invalidResponse'))}
 expect(fetch.mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(1)
})
it.each(['foreign','malformed-hash','missing-commit'])('%s success is unconfirmed, retains an error resource and reads original state once',async kind=>{
 const value={commit:kind==='malformed-hash'?'invalid':head,repository:{...repo,...(kind==='foreign'?{root:'/other'}:{})}}
 const fetch=vi.fn(async(_path:RequestInfo|URL,init?:RequestInit)=>Response.json(init?.method==='POST'?(kind==='missing-commit'?{repository:repo}:value):repo));vi.stubGlobal('fetch',fetch)
 const result=await writeGitOnce('/sessions/one/git','/fixture','commit',{}).result
 expect(result.confirmed).toBe(false);expect(result.repository).toEqual(repo);expect(result.error).toBeTruthy();expect(fetch).toHaveBeenCalledTimes(2)
})
it('a confirmed current-root commit does not issue a readback',async()=>{
 const fetch=vi.fn(async()=>Response.json({commit:head,repository:repo}));vi.stubGlobal('fetch',fetch)
 expect(await writeGitOnce('/sessions/one/git','/fixture','commit',{}).result).toEqual({confirmed:true,commit:head,repository:repo})
 expect(fetch).toHaveBeenCalledTimes(1)
})
it('an explicit refusal does not perform a readback or retry',async()=>{
 const fetch=vi.fn(async()=>Response.json({message:'Original refusal 原文'},{status:409}));vi.stubGlobal('fetch',fetch)
 const result=await writeGitOnce('/sessions/one/git','/fixture','stage',{}).result
 expect(result).toMatchObject({confirmed:false,error:'Original refusal 原文'});expect(fetch).toHaveBeenCalledTimes(1)
})
it('failed readback stays unknown and cannot borrow another repository',async()=>{
 const fetch=vi.fn(async(_path:RequestInfo|URL,init?:RequestInit)=>init?.method==='POST'?new Response('broken'):Response.json({...repo,root:'/other'}));vi.stubGlobal('fetch',fetch)
 const result=await writeGitOnce('/sessions/one/git','/fixture','stage',{}).result
 expect(result.confirmed).toBe(false);expect(result.repository).toBeUndefined();expect(result.error).toMatchObject({template:{key:'ui.git.operationUnconfirmedReadFailed'}})
 expect(fetch).toHaveBeenCalledTimes(2)
})
it('foreign patch receipt is rejected once and keeps a bilingual resource',async()=>{
 const fetch=vi.fn(async()=>Response.json({path:'other.txt',diff:'Original',binary:false,truncated:false}));vi.stubGlobal('fetch',fetch)
 const error=await readGitPatch('/sessions/one/git','original.txt','revision',new AbortController().signal).catch(error=>error)
 expect(failureText(error)).toEqual({key:'ui.http.invalidResponse'});expect(fetch).toHaveBeenCalledTimes(1)
})

it('a post-commit Hook may move HEAD without invalidating the confirmed commit',async()=>{
 const saved={...repo,head:'b'.repeat(40)}
 const fetch=vi.fn(async()=>Response.json({commit:head,repository:saved}));vi.stubGlobal('fetch',fetch)
 expect(await writeGitOnce('/sessions/one/git','/fixture','commit',{}).result).toEqual({confirmed:true,commit:head,repository:saved})
 expect(fetch).toHaveBeenCalledTimes(1)
})
it('an explicit business uncertainty at HTTP 400 still reads back and never replays',async()=>{
 const fetch=vi.fn(async(_path:RequestInfo|URL,init?:RequestInit)=>init?.method==='POST'?Response.json({code:'git/commit-uncertain',message:'Original backend copy'},{status:400}):Response.json(repo));vi.stubGlobal('fetch',fetch)
 const result=await writeGitOnce('/sessions/one/git','/fixture','commit',{}).result
 expect(result.confirmed).toBe(false);expect(result.repository).toEqual(repo);expect(fetch).toHaveBeenCalledTimes(2)
 expect(result.error).toMatchObject({template:{params:{reason:{key:'ui.git.error.commitUncertain'}}}})
})
it('known product refusals resolve on render and keep the original no-write outcome',async()=>{
 const fetch=vi.fn(async()=>Response.json({code:'git/index-changed',message:'Backend translation'},{status:409}));vi.stubGlobal('fetch',fetch)
 const result=await writeGitOnce('/sessions/one/git','/fixture','stage',{}).result
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(result.error!)).toBe(t('ui.git.error.indexChanged'))}
 expect(fetch).toHaveBeenCalledTimes(1)
})
