import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {api,loadCatalogs,type ApiFailure} from '@/lib/api'
import {useAppStore} from '../app'
import {useDraftStore} from '../drafts'
import {getFileRoot} from '@/lib/fileRoot'
import {getLocale,setLocale} from '@/i18n'
vi.mock('@/lib/theme',async original=>({...await original<typeof import('@/lib/theme')>(),applyTheme:vi.fn()}))
vi.mock('@/lib/api',async original=>({...await original<typeof import('@/lib/api')>(),loadCatalogs:vi.fn().mockResolvedValue({status:null,tools:[],skills:[],workspace:null})}))
const pending=<T,>()=>{let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return {resolve,promise}}
const failure:ApiFailure={ok:false,status:503,code:'offline',message:'External 原文'}
const workspace={id:'workspace',path:'/fixture',color:'blue',name:'Project 原文'}
beforeEach(()=>{const localValues=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>localValues.get(key)??null,setItem:(key:string,value:string)=>localValues.set(key,value),removeItem:(key:string)=>localValues.delete(key)})
vi.mocked(loadCatalogs).mockResolvedValue({status:null,tools:[],skills:[],workspace:null,errors:[]});vi.stubGlobal('document',{documentElement:{style:{}}});useDraftStore.setState({bySession:{},submissions:{}});useAppStore.setState({booting:false,booted:false,activeSessionId:null,activeWorkspaceId:'workspace',newSessionCheckout:null,newSessionCheckoutStatus:'ready',newSessionRevision:1,sessionCreating:false,sessions:[],workspaces:[workspace],sessionsLoading:false,toasts:[]})})
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})
it.each(['empty', 'fallback', 'persisted'] as const)('startup drops stale in-memory selection for an %s catalog',async kind=>{
 useAppStore.setState({activeSessionId:'deleted',sessions:[{id:'deleted',cwd:'/deleted'}]})
 localStorage.setItem('harness.active-session.v1',kind === 'persisted' ? 'saved' : 'deleted')
 vi.spyOn(api,'listSessions').mockResolvedValue({ok:true,data:{sessions:kind === 'empty' ? [] : [{id:'first',cwd:'/first'},{id:'saved',cwd:'/saved'}]}})
 vi.spyOn(api,'listWorkspaces').mockResolvedValue({ok:true,data:{workspaces:[workspace],default:'/fixture'}})
 vi.spyOn(api,'models').mockResolvedValue(failure);vi.spyOn(api,'settings').mockResolvedValue(failure)
 const details=vi.spyOn(api,'getSession')
 await useAppStore.getState().boot()
 const selected=kind === 'empty' ? null : kind === 'persisted' ? 'saved' : 'first'
 expect(useAppStore.getState().activeSessionId).toBe(selected)
 expect(localStorage.getItem('harness.active-session.v1')).toBe(selected)
 expect(getFileRoot()).toBe(selected ? '/' + selected : null)
 expect(useAppStore.getState()).toMatchObject({bootError:null,booted:true,toasts:[]})
 expect(details).not.toHaveBeenCalled()
})
it('complete backend failure exposes a global retry and preserves drafts and confirmed data until recovery',async()=>{
 useAppStore.setState({activeSessionId:'existing',sessions:[{id:'existing',cwd:'/fixture'}]})
 useDraftStore.getState().setText('__unsaved__','Keep 原文')
 vi.spyOn(api,'listSessions').mockResolvedValue(failure)
 vi.spyOn(api,'listWorkspaces').mockResolvedValue(failure)
 vi.spyOn(api,'models').mockResolvedValue(failure);vi.spyOn(api,'settings').mockResolvedValue(failure)
 await useAppStore.getState().boot()
 expect(useAppStore.getState()).toMatchObject({booting:false,bootError:{key:'ui.boot.backendUnavailable'},activeSessionId:'existing',sessions:[{id:'existing',cwd:'/fixture'}]})
 vi.mocked(api.listSessions).mockResolvedValue({ok:true,data:{sessions:[]}})
 vi.mocked(api.listWorkspaces).mockResolvedValue({ok:true,data:{workspaces:[workspace],default:'/fixture'}})
 const retry=useAppStore.getState().boot()
 expect(useAppStore.getState()).toMatchObject({booting:true,bootError:{key:'ui.boot.backendUnavailable'}})
 await retry
 expect(useAppStore.getState()).toMatchObject({booting:false,bootError:null,activeSessionId:null})
 expect(useDraftStore.getState().bySession.__unsaved__.text).toBe('Keep 原文')
})
it('closing settings refreshes credentials and an earlier close cannot overwrite the latest status',async()=>{
 useAppStore.setState({settingsOpen:false,status:{has_model_credentials:false}})
 const old=pending<Awaited<ReturnType<typeof api.status>>>()
 const current=pending<Awaited<ReturnType<typeof api.status>>>()
 const read=vi.spyOn(api,'status').mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
 useAppStore.getState().setSettingsOpen(true,'models')
 useAppStore.getState().setSettingsOpen(false)
 expect(useAppStore.getState().status).toBeNull()
 useAppStore.getState().setSettingsOpen(true,'models')
 useAppStore.getState().setSettingsOpen(false)
 current.resolve({ok:true,data:{has_model_credentials:true,config:{has_api_key:false}}})
 await vi.waitFor(()=>expect(useAppStore.getState().status?.has_model_credentials).toBe(true))
 old.resolve({ok:true,data:{has_model_credentials:false}})
 await old.promise
 expect(useAppStore.getState().status?.has_model_credentials).toBe(true)
 expect(read).toHaveBeenCalledTimes(2)
})
it('a list issued before creation cannot erase the created conversation',async()=>{
 const list=pending<Awaited<ReturnType<typeof api.listSessions>>>()
 vi.spyOn(api,'listSessions').mockReturnValueOnce(list.promise)
 vi.spyOn(api,'createSession').mockImplementation(async(_body,id)=>({ok:true,data:{session:{id:'created',cwd:'/fixture',metadata:{creation_request_id:id,creation_completed:true}}}}))
 const read=useAppStore.getState().refreshSessions()
 expect(await useAppStore.getState().materializeSession()).toBe(true)
 list.resolve({ok:true,data:{sessions:[]}});await read
 expect(useAppStore.getState().sessions.map(row=>row.id)).toEqual(['created'])
 expect(useAppStore.getState().activeSessionId).toBe('created')
 expect(useAppStore.getState().sessionsLoading).toBe(false)
})
it('a catalog that already includes the accepted creation does not duplicate the same id',async()=>{
 const write=pending<Awaited<ReturnType<typeof api.createSession>>>()
 vi.spyOn(api,'createSession').mockReturnValueOnce(write.promise)
 vi.spyOn(api,'listSessions').mockResolvedValue({ok:true,data:{sessions:[{id:'created',cwd:'/fixture',resolved_cwd:'/resolved',title:'Current 原文'},{id:'other'}]}})
 const create=useAppStore.getState().materializeSession();await useAppStore.getState().refreshSessions()
 write.resolve({ok:true,data:{session:{id:'created',cwd:'/fixture',metadata:{creation_request_id:vi.mocked(api.createSession).mock.calls[0][1],creation_completed:true}}}});await create
 expect(useAppStore.getState().sessions.map(row=>row.id)).toEqual(['created','other'])
 expect(useAppStore.getState().sessions[0].title).toBe('Current 原文')
 expect(getFileRoot()).toBe('/resolved')
})
it.each(['session','workspace'] as const)('a delayed startup catalog cannot restore a deleted %s',async kind=>{
 useAppStore.setState({activeSessionId:'original',sessions:[{id:'original',cwd:'/fixture'}]})
 const list=pending<Awaited<ReturnType<typeof api.listSessions>>>()
 const projects=pending<Awaited<ReturnType<typeof api.listWorkspaces>>>()
 vi.spyOn(api,'listSessions').mockReturnValueOnce(list.promise)
 vi.spyOn(api,'listWorkspaces').mockReturnValueOnce(projects.promise)
 vi.spyOn(api,'models').mockResolvedValue(failure);vi.spyOn(api,'settings').mockResolvedValue(failure)
 vi.spyOn(api,'deleteSession').mockResolvedValue({ok:true,data:{deleted:'original'}})
 vi.spyOn(api,'unregisterWorkspace').mockResolvedValue({ok:true,data:{deleted:'workspace'}})
 const boot=useAppStore.getState().boot()
 if(kind==='session')await useAppStore.getState().removeSession('original')
 else await useAppStore.getState().removeWorkspace('workspace')
 list.resolve({ok:true,data:{sessions:[{id:'original',cwd:'/fixture'}]}})
 projects.resolve({ok:true,data:{workspaces:[workspace],default:'/fixture'}})
 await boot
 expect(useAppStore.getState().bootError).toBeNull()
 expect(useAppStore.getState().booted).toBe(true)
 if(kind==='session'){expect(useAppStore.getState().sessions).toEqual([]);expect(useAppStore.getState().activeSessionId).toBeNull()}
 else expect(useAppStore.getState().workspaces).toEqual([])
})

it.each([
 [null, null, null],
 [undefined, 'workspace', '/fixture'],
 ['other', 'other', '/other'],
] as const)('new conversation project %s preserves explicit scope',async(selection,expectedId,expectedCwd)=>{
 useAppStore.setState({activeSessionId:'original',sessions:[{id:'original',cwd:'/fixture'}],activeWorkspaceId:'workspace',
   workspace:{root:'/default',path:'',parents:[],parent:'',exists:true,is_dir:true,truncated:false,entries:[]},workspaces:[workspace,{...workspace,id:'other',path:'/other'}]})
 const write=vi.spyOn(api,'createSession').mockImplementation(async(_body,id)=>({ok:true,data:{session:{id:'created',cwd:expectedCwd,metadata:{creation_request_id:id,creation_completed:true}}}}))
 useAppStore.getState().startNewSession(selection)
 expect(useAppStore.getState().activeWorkspaceId).toBe(expectedId)
 expect(getFileRoot()).toBe(expectedCwd)
 if (expectedId === null) {
  expect(useAppStore.getState().workspacePickerOpen).toBe(true)
  expect(await useAppStore.getState().materializeSession()).toBe(false)
  expect(write).not.toHaveBeenCalled()
  return
 }
 useAppStore.getState().setNewSessionCheckout(null, 'ready')
 expect(await useAppStore.getState().materializeSession()).toBe(true)
 expect(write).toHaveBeenCalledExactlyOnceWith({cwd:expectedCwd},expect.any(String))
})

it.each([['en','zh-CN'],['zh-CN','en']] as const)('late %s startup cannot undo confirmed %s language or catalog prose',async(initial,selected)=>{
 const prose=selected==='zh-CN'?'当前中文':'Current English'
 setLocale(initial);useAppStore.setState({language:initial,effectiveLanguage:initial,languageSaving:false,tools:[],skills:[],models:[]})
 const old=pending<Awaited<ReturnType<typeof loadCatalogs>>>()
 const current={status:null,tools:[{name:'tool',description:prose}],skills:[{name:'skill',description:prose}],workspace:null,errors:[]}
 vi.mocked(loadCatalogs).mockReturnValueOnce(old.promise).mockResolvedValue(current)
 vi.spyOn(api,'listSessions').mockResolvedValue({ok:true,data:{sessions:[]}})
 vi.spyOn(api,'listWorkspaces').mockResolvedValue({ok:true,data:{workspaces:[],default:''}})
 const oldModels={ok:true as const,data:{models:[{id:'model',provider:'fixture',label:'Stale English'}],active:'model',providers:[]}}
 const newModels={ok:true as const,data:{models:[{id:'model',provider:'fixture',label:prose}],active:'model',providers:[]}}
 vi.spyOn(api,'models').mockResolvedValueOnce(oldModels).mockResolvedValue(newModels)
 vi.spyOn(api,'settings').mockResolvedValue({ok:true,data:{settings:{language:initial,effective_language:initial}}})
 vi.spyOn(api,'tools').mockResolvedValue({ok:true,data:{tools:current.tools}})
 vi.spyOn(api,'skills').mockResolvedValue({ok:true,data:{skills:current.skills}})
 const write=vi.spyOn(api,'updateSettings').mockResolvedValue({ok:true,data:{settings:{language:selected,effective_language:selected},rejected:[]}})
 const boot=useAppStore.getState().boot()
 await useAppStore.getState().setLanguage(selected)
 old.resolve({...current,tools:[{name:'tool',description:'Stale English'}],skills:[{name:'skill',description:'Stale English'}]})
 await boot
 expect(useAppStore.getState()).toMatchObject({language:selected,effectiveLanguage:selected,bootError:null,booted:true})
 expect(getLocale()).toBe(selected)
 expect(useAppStore.getState().tools[0].description).toBe(prose)
 expect(useAppStore.getState().skills[0].description).toBe(prose)
 expect(useAppStore.getState().models[0].label).toBe(prose)
 expect(write).toHaveBeenCalledTimes(1)
 setLocale('en')
})
