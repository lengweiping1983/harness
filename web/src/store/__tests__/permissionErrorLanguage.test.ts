import {afterEach,beforeEach,expect,test,vi} from 'vitest'
vi.mock('@/lib/http',async original=>({...await original<typeof import('@/lib/http')>(),runtimeRequest:vi.fn()}))
import {runtimeRequest,ApiError} from '@/lib/http'
import {setLocale,t} from '@/i18n'
import {noticeText} from '@/lib/noticeText'
import {usePermissionStore} from '../permissions'
const request=vi.mocked(runtimeRequest)
beforeEach(()=>usePermissionStore.setState({policy:'request_approval',sessionId:'owner',scope:'owner',version:'a'.repeat(64),loading:false,busy:false,error:null,readError:null,saveError:null,saveUnconfirmed:false}))
afterEach(()=>{request.mockReset();setLocale('zh-CN')})
function language(field:'readError'|'saveError',key:string){
 const copy=usePermissionStore.getState()[field]
 if(!copy)throw Error('Expected saved error')
 const count=request.mock.calls.length
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t(key));expect(request).toHaveBeenCalledTimes(count)}
}
test('retains read transport error without fetching again',async()=>{
 request.mockRejectedValue(new ApiError({ok:false,status:200,code:'response/invalid-json',message:'snapshot',copy:{key:'ui.http.invalidResponse'}}))
 await usePermissionStore.getState().load('owner')
 language('readError','ui.http.invalidResponse')
 expect(usePermissionStore.getState().loading).toBe(false)
})
test('refuses parent-controlled permission writes without a request',async()=>{
 usePermissionStore.setState({scope:'parent'})
 expect(await usePermissionStore.getState().save('full_access')).toBe(false)
 language('saveError','ui.store.permissions.1debdc254a70')
 expect(request).not.toHaveBeenCalled();expect(usePermissionStore.getState().saveUnconfirmed).toBe(false)
})
test('retains definitive refusals and updated conflict versions',async()=>{
 request.mockRejectedValue(new ApiError({ok:false,status:409,code:'permissions/changed',message:'snapshot',copy:{key:'ui.http.invalidResponse'},details:{permission_policy:'read_only',version:'b'.repeat(64),inherited:false}}))
 expect(await usePermissionStore.getState().save('full_access')).toBe(false)
 language('saveError','ui.http.invalidResponse')
 expect(usePermissionStore.getState()).toMatchObject({version:'b'.repeat(64),policy:'read_only',saveUnconfirmed:false,busy:false})
 expect(request).toHaveBeenCalledTimes(1)
})
test('does not reinterpret an unconfirmed write or repeat it when language changes',async()=>{
 request.mockRejectedValue(new ApiError({ok:false,status:0,code:'network',message:'snapshot',copy:{key:'ui.context.saveUnknown'}}))
 expect(await usePermissionStore.getState().save('full_access')).toBe(false)
 language('saveError','ui.context.saveUnknown')
 expect(usePermissionStore.getState()).toMatchObject({saveUnconfirmed:true,busy:false,policy:'request_approval'})
 expect(request).toHaveBeenCalledTimes(1)
})
test('late read failure cannot replace another conversation permissions',async()=>{
 let reject!:(reason:unknown)=>void
 request.mockImplementationOnce(()=>new Promise((_,fail)=>{reject=fail})).mockResolvedValueOnce({permissions:{session_id:'other',scope:'other',permission_policy:'read_only',version:'c'.repeat(64),inherited:false}})
 const old=usePermissionStore.getState().load('owner')
 await usePermissionStore.getState().load('other')
 reject(new Error('Late original diagnostic'));await old
 expect(usePermissionStore.getState()).toMatchObject({sessionId:'other',policy:'read_only',readError:null,saveError:null})
})
