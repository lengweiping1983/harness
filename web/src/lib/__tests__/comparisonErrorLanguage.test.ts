import {afterEach, describe, expect, it, vi} from 'vitest'
vi.mock('../http',async original=>({...await original<typeof import('../http')>(),runtimeRequest:vi.fn()}))
import {runtimeRequest} from '../http'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'
import {setLocale,t} from '@/i18n'
import {readComparison,readComparisonPatch,type ChangeSnapshot,type ChangeFile} from '../comparison'
import {readTurnChanges} from '../turnChanges'
import {readNetChanges} from '../netChanges'
const request=vi.mocked(runtimeRequest)
const query={scope:'uncommitted',base:'',target:''} as const
const signal=()=>new AbortController().signal
const file={path:'Original 文件.txt',status:'M',added:1,deleted:0}
const page={files:[file],total:2,next_offset:1,revision:'first',left:'HEAD',right:'WORKTREE',workspace:'/original',added:1,deleted:0}
async function rejected(action:()=>Promise<unknown>){try{await action()}catch(error){return error}throw Error('Expected rejection')}
function language(error:unknown,key:string){
 const copy=failureText(error),count=request.mock.calls.length
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t(key));expect(request).toHaveBeenCalledTimes(count)}
}
afterEach(()=>{request.mockReset();setLocale('zh-CN')})
describe('comparison rejection identities',()=>{
 it('rejects missing history before any read',async()=>{
  language(await rejected(()=>readComparison('owner',{...query,scope:'history'},null,signal())),'ui.changes.noHistory')
  expect(request).not.toHaveBeenCalled()
 })
 it('rejects changed revisions during pagination and keeps its conflict code',async()=>{
  request.mockResolvedValueOnce(page).mockResolvedValueOnce({...page,revision:'second',next_offset:null})
  const error=await rejected(()=>readComparison('owner',query,null,signal()))
  expect(error).toMatchObject({status:409,code:'review/changed'})
  language(error,'ui.changes.changed');expect(request).toHaveBeenCalledTimes(2)
 })
 it('rejects duplicate paths instead of presenting an inconsistent list',async()=>{
  request.mockResolvedValueOnce({...page,files:[file,file],next_offset:null})
  language(await rejected(()=>readComparison('owner',query,null,signal())),'ui.changes.changed')
 })
 it('rejects a patch from another path without changing the selected file',async()=>{
  request.mockResolvedValueOnce({...page,selection:{path:'Other.txt'},next_offset:null})
  language(await rejected(()=>readComparisonPatch('owner',query,{revision:'first'} as ChangeSnapshot,file,signal())),'ui.changes.changed')
  expect(file.path).toBe('Original 文件.txt');expect(request).toHaveBeenCalledTimes(1)
 })
 it('does not read an unavailable historical file',async()=>{
  const selected={...file,history:{available:false}} as ChangeFile
  const snapshot={historyTarget:{sessionId:'owner',turn:1,seq:0},through:7} as ChangeSnapshot
  language(await rejected(()=>readComparisonPatch('owner',query,snapshot,selected,signal())),'ui.changes.historyUnavailable')
  expect(request).not.toHaveBeenCalled()
 })
 for(const [name,read,key] of [['operations',readTurnChanges,'ui.lib.turnChanges.1ed5550aaff5'],['net',readNetChanges,'ui.lib.netChanges.502f59eea0d1']] as const){
  it(`rejects invalid ${name} targets before any request`,async()=>{
   const error=await rejected(()=>read({sessionId:'owner',turn:0,seq:0}))
   expect(error).toMatchObject({status:409,code:'changes/invalid-target'})
   language(error,key);expect(request).not.toHaveBeenCalled()
  })
 }
 it('retains valid empty comparisons and resource paths',async()=>{
  request.mockResolvedValueOnce({...page,files:[],total:0,next_offset:null})
  const snapshot=await readComparison('owner',query,null,signal())
  expect(snapshot).toMatchObject({files:[],total:0,revision:'first',root:'/original'})
  expect(request).toHaveBeenCalledTimes(1)
 })
})
