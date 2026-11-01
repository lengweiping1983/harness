import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {api} from '@/lib/api'
import {useAppStore} from '../app'
const pending=<T,>()=>{let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return {resolve,promise}}
const workspace={id:'workspace',path:'/fixture',color:'blue',name:'Original 原文'}
beforeEach(()=>useAppStore.setState({sessions:[{id:'session',cwd:'/fixture'}],activeSessionId:'session',workspaces:[workspace],activeWorkspaceId:'workspace',sessionsLoading:false,workspacesLoading:false,toasts:[]}))
afterEach(()=>vi.restoreAllMocks())
it('a late older workspace catalog cannot replace a newer refresh',async()=>{
 const old=pending<Awaited<ReturnType<typeof api.listWorkspaces>>>()
 vi.spyOn(api,'listWorkspaces').mockReturnValueOnce(old.promise).mockResolvedValueOnce({ok:true,data:{workspaces:[{...workspace,name:'Current 原文'}],default:'workspace'}})
 const first=useAppStore.getState().refreshWorkspaces()
 await useAppStore.getState().refreshWorkspaces()
 old.resolve({ok:true,data:{workspaces:[workspace],default:'workspace'}});await first
 expect(useAppStore.getState().workspaces[0].name).toBe('Current 原文')
 expect(useAppStore.getState().workspacesLoading).toBe(false)
})
it('a late failed workspace read does not announce failure over a newer successful refresh',async()=>{
 const old=pending<Awaited<ReturnType<typeof api.listWorkspaces>>>()
 vi.spyOn(api,'listWorkspaces').mockReturnValueOnce(old.promise).mockResolvedValueOnce({ok:true,data:{workspaces:[workspace],default:'workspace'}})
 const first=useAppStore.getState().refreshWorkspaces();await useAppStore.getState().refreshWorkspaces()
 old.resolve({ok:false,status:503,code:'offline',message:'Stale error'});await first
 expect(useAppStore.getState().toasts).toEqual([])
})
it('an earlier catalog cannot resurrect an unregistered workspace',async()=>{
 const old=pending<Awaited<ReturnType<typeof api.listWorkspaces>>>()
 vi.spyOn(api,'listWorkspaces').mockReturnValueOnce(old.promise)
 vi.spyOn(api,'unregisterWorkspace').mockResolvedValue({ok:true,data:{deleted:'workspace'}})
 const read=useAppStore.getState().refreshWorkspaces();await useAppStore.getState().removeWorkspace('workspace')
 old.resolve({ok:true,data:{workspaces:[workspace],default:'workspace'}});await read
 expect(useAppStore.getState().workspaces).toEqual([])
 expect(useAppStore.getState().workspacesLoading).toBe(false)
})
it('an earlier catalog cannot resurrect a deleted conversation',async()=>{
 const old=pending<Awaited<ReturnType<typeof api.listSessions>>>()
 vi.spyOn(api,'listSessions').mockReturnValueOnce(old.promise)
 vi.spyOn(api,'deleteSession').mockResolvedValue({ok:true,data:{deleted:'session'}})
 const read=useAppStore.getState().refreshSessions();await useAppStore.getState().removeSession('session')
 old.resolve({ok:true,data:{sessions:[{id:'session',cwd:'/fixture'}]}});await read
 expect(useAppStore.getState().sessions).toEqual([])
 expect(useAppStore.getState().activeSessionId).toBeNull()
 expect(useAppStore.getState().sessionsLoading).toBe(false)
})
