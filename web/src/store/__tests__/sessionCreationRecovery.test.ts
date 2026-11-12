import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {api,type ApiFailure} from '@/lib/api'
import {setLocale,t} from '@/i18n'
import {noticeText} from '@/lib/noticeText'
import {validPersistedDrafts} from '@/lib/draftPersistence'
import type {SessionDetail,SessionSummary} from '@/types'
import {useAppStore} from '../app'
import {useDraftStore} from '../drafts'
const unknown:ApiFailure={ok:false,status:0,code:'network',message:'Original network'}
const row=(id:string,complete=true):SessionSummary=>({id:'created',cwd:'/fixture',metadata:{creation_request_id:id,creation_completed:complete}})
const detail=(header:SessionSummary):SessionDetail=>({header,events:[],messages:[],usage:{},stats:{},todos:[],timeline:[]})
beforeEach(()=>{const localValues=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>localValues.get(key)??null,setItem:(key:string,value:string)=>localValues.set(key,value),removeItem:(key:string)=>localValues.delete(key)})
useDraftStore.setState({bySession:{},submissions:{}});useAppStore.setState({activeSessionId:null,activeWorkspaceId:'project',newSessionCheckout:null,newSessionCheckoutStatus:'ready',newSessionRevision:1,sessionCreating:false,sessions:[],workspaces:[{id:'project',path:'/fixture',name:'Project',color:'blue'}],toasts:[]})})
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();setLocale('en')})
it.each(['lost','mismatched'] as const)('%s response recovers only the exact completed identity without another write',async(kind)=>{
 let id=''
 const write=vi.spyOn(api,'createSession').mockImplementation(async(_body,requestId)=>{id=requestId!;return kind==='lost'?unknown:{ok:true,data:{session:row('unrelated')}}})
 const list=vi.spyOn(api,'listSessions').mockImplementation(async()=>({ok:true,data:{sessions:[{id:'lookalike',cwd:'/fixture',title:'same title'},row(id)]}}))
 const read=vi.spyOn(api,'getSession').mockImplementation(async()=>({ok:true,data:detail(row(id))}))
 const submission={id:'send-original',text:'Original 用户 {name}',attachments:[],action:'send' as const,status:'sending' as const}
 expect(await useAppStore.getState().materializeSession(submission)).toBe(true)
 expect(write.mock.calls[0][0]).toEqual({cwd:'/fixture'})
 expect(write).toHaveBeenCalledTimes(1);expect(list).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledExactlyOnceWith('created','conversation-window')
 expect(useAppStore.getState().activeSessionId).toBe('created')
 expect(useDraftStore.getState().submissions.created).toHaveLength(1)
 expect(useDraftStore.getState().submissions.created[0].text).toBe(submission.text)
 expect(useDraftStore.getState().bySession.__unsaved__.creationRequest).toBeUndefined()
})
it.each(['missing','incomplete','duplicate','wrong-header'] as const)('%s readback stays pending and subsequent send only reads',async(kind)=>{
 let id=''
 const write=vi.spyOn(api,'createSession').mockImplementation(async(_body,requestId)=>{id=requestId!;return unknown})
 vi.spyOn(api,'listSessions').mockImplementation(async()=>({ok:true,data:{sessions:kind==='missing'?[]:kind==='duplicate'?[row(id),{...row(id),id:'duplicate'}]:[row(id,kind!=='incomplete')]}}))
 vi.spyOn(api,'getSession').mockImplementation(async()=>({ok:true,data:detail({...row(id),id:'other'})}))
 expect(await useAppStore.getState().materializeSession()).toBe(false)
 const draft=useDraftStore.getState().bySession.__unsaved__
 const snapshot=JSON.stringify({version:0,state:useDraftStore.persist.getOptions().partialize!(useDraftStore.getState())})
 expect(validPersistedDrafts(snapshot)).toBe(true)
 expect(JSON.parse(snapshot).state.bySession.__unsaved__.creationRequest).toEqual(draft.creationRequest)
 expect(await useAppStore.getState().materializeSession()).toBe(false)
 expect(write).toHaveBeenCalledTimes(1)
 expect(useDraftStore.getState().bySession.__unsaved__.creationRequest?.id).toBe(id)
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(useAppStore.getState().toasts.at(-1)!.description!)).toBe(t('ui.sessionCreation.unconfirmed'))}
})
it('a pending request can be confirmed later without replaying creation',async()=>{
 let id=''
 const write=vi.spyOn(api,'createSession').mockImplementation(async(_body,requestId)=>{id=requestId!;return unknown})
 vi.spyOn(api,'listSessions').mockResolvedValueOnce({ok:true,data:{sessions:[]}}).mockImplementation(async()=>({ok:true,data:{sessions:[row(id)]}}))
 vi.spyOn(api,'getSession').mockImplementation(async()=>({ok:true,data:detail(row(id))}))
 expect(await useAppStore.getState().materializeSession()).toBe(false)
 expect(await useAppStore.getState().materializeSession()).toBe(true)
 expect(write).toHaveBeenCalledTimes(1)
})
it('a confirmed refusal releases the attempt without a verification read',async()=>{
 const write=vi.spyOn(api,'createSession').mockResolvedValue({...unknown,status:403,code:'access/denied'})
 const list=vi.spyOn(api,'listSessions')
 expect(await useAppStore.getState().materializeSession()).toBe(false)
 expect(useDraftStore.getState().bySession.__unsaved__.creationRequest).toBeUndefined()
 expect(write).toHaveBeenCalledTimes(1);expect(list).not.toHaveBeenCalled()
})
it('unavailable browser storage prevents creation before any accepted write',async()=>{
 vi.stubGlobal('localStorage',{getItem:()=>null,setItem:()=>{throw Error('storage blocked')}})
 const write=vi.spyOn(api,'createSession'),list=vi.spyOn(api,'listSessions')
 expect(await useAppStore.getState().materializeSession()).toBe(false)
 expect(write).not.toHaveBeenCalled();expect(list).not.toHaveBeenCalled()
 expect(useDraftStore.getState().bySession.__unsaved__.creationRequest).toBeUndefined()
})
it.each(['project', 'agent', 'checkout'] as const)('recovery after changing %s submits once to the current scope in the same send',async kind=>{
 const old = {id: 'old-request', cwd: '/fixture'}
 useDraftStore.getState().setCreationRequest(old)
 if(kind === 'project') useAppStore.setState({activeWorkspaceId:'other',workspaces:[{id:'other',path:'/other',name:'Other',color:'blue'}]})
 if(kind === 'agent') useDraftStore.getState().setAgentSelection('__unsaved__',{name:'current-agent',agent_type:'agent'})
 if(kind === 'checkout') useAppStore.getState().setNewSessionCheckout({cwd:'/fixture',mode:'worktree',ref:'feature',expected_head:'abc',expected_branch:'main'})
 vi.spyOn(api,'listSessions').mockResolvedValue({ok:true,data:{sessions:[row(old.id)]}})
 vi.spyOn(api,'getSession').mockResolvedValue({ok:true,data:detail(row(old.id))})
 const write=vi.spyOn(api,'createSession').mockImplementation(async(body,id)=>({ok:true,data:{session:{id:'current',cwd:body?.cwd,metadata:{creation_request_id:id,creation_completed:true}},
  ...(kind === 'agent' ? {agent_mount:{available:true,agent:'current-agent',revision:1}} : {})}}))
 const submission={id:'send-current',text:'Current 原文',attachments:[],action:'send' as const,status:'sending' as const}
 expect(await useAppStore.getState().materializeSession(submission)).toBe(true)
 expect(write).toHaveBeenCalledTimes(1)
 expect(write.mock.calls[0][1]).not.toBe(old.id)
 expect(write.mock.calls[0][0]?.cwd).toBe(kind === 'project' ? '/other' : '/fixture')
 if(kind === 'agent') expect(write.mock.calls[0][0]?.agent_selection).toBe('current-agent')
 if(kind === 'checkout') expect(write.mock.calls[0][0]?.project).toMatchObject({workspace_id:'project',mode:'worktree',ref:'feature'})
 expect(useAppStore.getState().activeSessionId).toBe('current')
 expect(useAppStore.getState().sessions.map(value=>value.id)).toEqual(['current','created'])
 expect(useDraftStore.getState().submissions.created).toBeUndefined()
 expect(useDraftStore.getState().submissions.current[0].text).toBe(submission.text)
 expect(useDraftStore.getState().bySession.__unsaved__.creationRequest).toBeUndefined()
})
it('changing project never discards an unknown creation or repeats its write',async()=>{
 useDraftStore.getState().setCreationRequest({id:'unknown-original',cwd:'/old'})
 vi.spyOn(api,'listSessions').mockResolvedValue({ok:true,data:{sessions:[]}})
 const write=vi.spyOn(api,'createSession')
 expect(await useAppStore.getState().materializeSession()).toBe(false)
 expect(await useAppStore.getState().materializeSession()).toBe(false)
 expect(write).not.toHaveBeenCalled()
 expect(useDraftStore.getState().bySession.__unsaved__.creationRequest?.id).toBe('unknown-original')
})
it('leaving the draft during old-scope recovery does not create or submit into the new scope',async()=>{
 useDraftStore.getState().setCreationRequest({id:'old-request',cwd:'/old'})
 let release!:(value:Awaited<ReturnType<typeof api.getSession>>)=>void
 vi.spyOn(api,'listSessions').mockResolvedValue({ok:true,data:{sessions:[row('old-request')]}})
 vi.spyOn(api,'getSession').mockReturnValue(new Promise(done=>{release=done}))
 const write=vi.spyOn(api,'createSession')
 const creating=useAppStore.getState().materializeSession()
 await vi.waitFor(()=>expect(api.getSession).toHaveBeenCalledOnce())
 useAppStore.getState().startNewSession(null)
 release({ok:true,data:detail(row('old-request'))})
 expect(await creating).toBe(false)
 expect(write).not.toHaveBeenCalled()
 expect(useAppStore.getState().activeSessionId).toBeNull()
 expect(useDraftStore.getState().submissions.created).toBeUndefined()
})
it('an unconfirmed Agent mount retains the creation identity until its readback is available',async()=>{
 let id='',mounted=false
 useDraftStore.getState().setAgentSelection('__unsaved__',{name:'agent-original',agent_type:'agent'})
 const write=vi.spyOn(api,'createSession').mockImplementation(async(_body,requestId)=>{id=requestId!;return unknown})
 vi.spyOn(api,'listSessions').mockImplementation(async()=>({ok:true,data:{sessions:[row(id)]}}))
 vi.spyOn(api,'getSession').mockImplementation(async()=>({ok:true,data:{...detail(row(id)),agent_mount:{available:mounted,agent:mounted?'agent-original':null,revision:1}}}))
 expect(await useAppStore.getState().materializeSession()).toBe(false)
 expect(useDraftStore.getState().bySession.__unsaved__.creationRequest?.id).toBe(id)
 mounted=true
 expect(await useAppStore.getState().materializeSession()).toBe(true)
 expect(useDraftStore.getState().bySession.__unsaved__.creationRequest).toBeUndefined()
 expect(write).toHaveBeenCalledTimes(1)
})
