import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {api,type ApiFailure} from '@/lib/api'
import {useAppStore} from '../app'
import {useDraftStore} from '../drafts'
import {setLocale,t} from '@/i18n'
import {noticeText} from '@/lib/noticeText'
const malformed:ApiFailure={ok:false,status:200,code:'response/invalid-json',message:'diagnostic',copy:{key:'ui.http.invalidResponse'}}
const workspace={id:'target',path:'/fixture',color:'blue',name:'User 原文'}
beforeEach(()=>{setLocale('en');useAppStore.setState({sessions:[{id:'target',cwd:'/fixture'}],activeSessionId:'target',workspaces:[workspace],activeWorkspaceId:'target',toasts:[]});useDraftStore.getState().setText('target','Original draft 原文')})
afterEach(()=>{vi.restoreAllMocks();setLocale('zh-CN')})
for(const kind of ['session','workspace'] as const){
 const operation=()=>kind==='session'?useAppStore.getState().removeSession('target'):useAppStore.getState().removeWorkspace('target')
 const write=()=>vi.spyOn(api,kind==='session'?'deleteSession':'unregisterWorkspace')
 const read=(present:boolean)=>kind==='session'?vi.spyOn(api,'listSessions').mockResolvedValue({ok:true,data:{sessions:present?[{id:'target'}]:[]}}):vi.spyOn(api,'listWorkspaces').mockResolvedValue({ok:true,data:{workspaces:present?[workspace]:[],default:'/fixture'}})
 it(`${kind}: a foreign deletion receipt cannot discard original state or draft`,async()=>{
  const remove=write().mockResolvedValue({ok:true,data:{deleted:'other'}}),list=read(true)
  await operation()
  expect((kind==='session'?useAppStore.getState().sessions:useAppStore.getState().workspaces).map(row=>row.id)).toContain('target')
  expect(useDraftStore.getState().bySession.target?.text).toBe('Original draft 原文')
  expect(remove).toHaveBeenCalledTimes(1);expect(list).toHaveBeenCalledTimes(1)
  const title=useAppStore.getState().toasts[0]!.title
  for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(title)).toBe(t(kind==='session'?'ui.session.deletionUnconfirmed':'ui.workspace.removalUnconfirmed'))}
 })
 it.each([malformed,...[0,408,500].map(status=>({...malformed,status})),{ok:true as const,data:{deleted:'other'}}])(`${kind}: an uncertain receipt confirms through one absence read`,async receipt=>{
  const remove=write().mockResolvedValue(receipt),list=read(false)
  await operation()
  expect(kind==='session'?useAppStore.getState().sessions:useAppStore.getState().workspaces).toEqual([])
  if(kind==='session')expect(useDraftStore.getState().bySession.target).toBeUndefined()
  expect(remove).toHaveBeenCalledTimes(1);expect(list).toHaveBeenCalledTimes(1)
 })
 it(`${kind}: a failed read retains the original state and draft`,async()=>{
  write().mockResolvedValue(malformed)
  const list=read(false);list.mockRejectedValue(new Error('External 原文'))
  await operation()
  expect((kind==='session'?useAppStore.getState().sessions:useAppStore.getState().workspaces).map(row=>row.id)).toContain('target')
  expect(useDraftStore.getState().bySession.target?.text).toBe('Original draft 原文')
 })
 it(`${kind}: an explicit rejection does not read or replay`,async()=>{
  const remove=write().mockResolvedValue({...malformed,status:403,code:'forbidden'}),list=read(false)
  await operation();expect(remove).toHaveBeenCalledTimes(1);expect(list).not.toHaveBeenCalled()
 })
}
