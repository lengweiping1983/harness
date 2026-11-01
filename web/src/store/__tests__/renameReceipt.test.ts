import {afterEach,beforeEach,expect,test,vi} from 'vitest'
import {api,type ApiFailure} from '@/lib/api'
import {useAppStore,nameSaveKey} from '../app'
import {setLocale,t} from '@/i18n'
import {noticeText} from '@/lib/noticeText'
const failure:ApiFailure={ok:false,status:200,code:'response/invalid-json',message:'snapshot',copy:{key:'ui.http.invalidResponse'}}
beforeEach(()=>useAppStore.setState({sessions:[{id:'owner',title:'Old 原文'}],workspaces:[{id:'workspace',path:'/fixture',color:'blue',name:'Old 原文'}],nameSaves:{}}))
afterEach(()=>{vi.restoreAllMocks();setLocale('zh-CN')})
test('a broken successful session rename receipt reads the exact title without replaying PATCH',async()=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue(failure),read=vi.spyOn(api,'getSession').mockResolvedValue({ok:true,data:{header:{id:'owner',title:'New 原文'},messages:[],events:[],usage:{},stats:{},todos:[],timeline:[]}})
 expect(await useAppStore.getState().renameSession('owner','New 原文')).toBeNull()
 expect(useAppStore.getState().sessions[0].title).toBe('New 原文');expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
})
test('workspace schema failure reads its own record and preserves other workspace identities',async()=>{
 const write=vi.spyOn(api,'updateWorkspace').mockResolvedValue({...failure,code:'response/invalid-schema'}),read=vi.spyOn(api,'listWorkspaces').mockResolvedValue({ok:true,data:{default:'workspace',workspaces:[{id:'workspace',path:'/fixture',color:'blue',name:'New 原文'}]}})
 expect(await useAppStore.getState().renameWorkspace('workspace','New 原文')).toBeNull()
 expect(useAppStore.getState().workspaces[0]).toMatchObject({id:'workspace',name:'New 原文'});expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
})
test('an unrelated record cannot confirm a rename and the unknown resource survives language changes',async()=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue(failure),read=vi.spyOn(api,'getSession').mockResolvedValue({ok:true,data:{header:{id:'other',title:'New 原文'},messages:[],events:[],usage:{},stats:{},todos:[],timeline:[]}})
 const result=await useAppStore.getState().renameSession('owner','New 原文')
 expect(result).toMatchObject({code:'rename/unconfirmed',status:0})
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(result!.copy!)).toBe(t('ui.store.app.8e4a2e5a35b6'))}
 expect(useAppStore.getState().sessions[0].title).toBe('Old 原文');expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
})
test('known workspace persistence refusal is definitive and never read back as success',async()=>{
 const write=vi.spyOn(api,'updateWorkspace').mockResolvedValue({...failure,status:500,code:'workspace/persist-failed'}),read=vi.spyOn(api,'listWorkspaces')
 expect(await useAppStore.getState().renameWorkspace('workspace','New 原文')).toMatchObject({code:'workspace/persist-failed'})
 expect(write).toHaveBeenCalledTimes(1);expect(read).not.toHaveBeenCalled()
})
test('empty and busy rename guards retain explicit resources without writes',async()=>{
 const write=vi.spyOn(api,'updateSession')
 const empty=await useAppStore.getState().renameSession('owner',' ')
 useAppStore.setState({nameSaves:{[nameSaveKey('session','owner')]:true}})
 const busy=await useAppStore.getState().renameSession('owner','New 原文')
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(empty!.copy!)).toBe(t('ui.store.app.090ec5f82dfc'));expect(noticeText(busy!.copy!)).toBe(t('ui.store.app.232d99bbaa75'))}
 expect(write).not.toHaveBeenCalled()
})
