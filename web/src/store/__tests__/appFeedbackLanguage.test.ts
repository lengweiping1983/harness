import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {api,type ApiFailure} from '@/lib/api'
import {setLocale,t} from '@/i18n'
import {noticeText} from '@/lib/noticeText'
import {useAppStore} from '../app'
import type {ResourceText} from '@/types'
const failure:ApiFailure={ok:false,status:200,code:'response/invalid-json',message:'snapshot',copy:{key:'ui.http.invalidResponse'}}
beforeEach(()=>{setLocale('en');useAppStore.setState({toasts:[],sessions:[],activeSessionId:null,workspaces:[{id:'workspace',path:'/fixture',color:'blue',name:'User 原文'}],languageSaving:false,languageError:null})})
afterEach(()=>{vi.restoreAllMocks();setLocale('zh-CN')})
function language(value:ResourceText|undefined,key:string,params?:Record<string,string>){for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(value??'')).toBe(t(key,params))}}
it.each([
 ['listSessions','refreshSessions','ui.store.app.6106f0a12583'],
 ['listWorkspaces','refreshWorkspaces','ui.store.app.a2de8299587b'],
 ['registerWorkspace','addWorkspace','ui.store.app.00751ace9d67'],
 ['unregisterWorkspace','removeWorkspace','ui.store.app.4757fc1d4709'],
 ['deleteSession','removeSession','ui.store.app.c228558cf257'],
] as const)('%s failure keeps title and reason resources',async(method,action,key)=>{
 vi.spyOn(api,method).mockResolvedValue(method==='unregisterWorkspace'||method==='deleteSession'?{...failure,status:403,code:'forbidden'}:failure)
 await useAppStore.getState()[action]('fixture')
 const toast=useAppStore.getState().toasts[0]!
 language(toast.title,key);language(toast.description,'ui.http.invalidResponse')
})
it('workspace removal keeps the original name inside a translated description',async()=>{
 vi.spyOn(api,'unregisterWorkspace').mockResolvedValue({ok:true,data:{deleted:'workspace'}})
 await useAppStore.getState().removeWorkspace('workspace')
 language(useAppStore.getState().toasts[0]!.description,'ui.store.app.d01bf10fbeee',{p0:'User 原文'})
})
it('a language persistence rejection remains a resource without another settings write',async()=>{
 const write=vi.spyOn(api,'updateSettings').mockResolvedValue({...failure,status:500,code:'settings/persist-failed'}),read=vi.spyOn(api,'settings')
 await useAppStore.getState().setLanguage('zh-CN')
 language(useAppStore.getState().languageError??undefined,'ui.settings.persistFailed')
 expect(write).toHaveBeenCalledTimes(1);expect(read).not.toHaveBeenCalled()
})
