import {afterEach, expect, it, vi} from 'vitest'
import {validPersistedDrafts} from '@/lib/draftPersistence'

const empty = () => ({state:{bySession:{},submissions:{}},version:0})
afterEach(async()=>{vi.unstubAllGlobals();(await import('@/i18n')).setLocale('zh-CN')})

it.each([
 '{','null',JSON.stringify({...empty(),version:1}),
 JSON.stringify({state:{...empty().state,setText:'injected'},version:0}),
 JSON.stringify({state:{bySession:{fixture:{text:3,attachments:[]}},submissions:{}},version:0}),
 JSON.stringify({state:{bySession:{fixture:{text:'original',attachments:[null]}},submissions:{}},version:0}),
 JSON.stringify({state:{bySession:{},submissions:{fixture:[{id:'request',text:'original',attachments:[],action:'send',status:'legacy'}]}},version:0}),
])('preserves damaged originals when isolation storage is unavailable: %s',async raw=>{
 vi.resetModules()
 const write=vi.fn((_key:string,_value:string)=>{throw Error('Storage unavailable')}),remove=vi.fn(),fetch=vi.fn()
 vi.stubGlobal('localStorage',{getItem:()=>raw,setItem:write,removeItem:remove});vi.stubGlobal('fetch',fetch)
 const {useDraftStore}=await import('../drafts')
 await Promise.resolve()
 expect(typeof useDraftStore.getState().setText).toBe('function')
 expect(useDraftStore.getState().bySession).toEqual({})
 useDraftStore.getState().setText('fixture','New input 原文')
 await useDraftStore.persist.rehydrate()
 expect(useDraftStore.getState().bySession.fixture.text).toBe('New input 原文')
 expect(useDraftStore.getState().storageError).toEqual({key:'ui.drafts.invalidStorage'})
 const {setLocale,t}=await import('@/i18n'),{noticeText}=await import('@/lib/noticeText')
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(useDraftStore.getState().storageError??'')).toBe(t('ui.drafts.invalidStorage'))}
 useDraftStore.persist.clearStorage()
 expect(write.mock.calls.filter(([key])=>key==='harness.drafts.v1')).toHaveLength(0);expect(remove).not.toHaveBeenCalled();expect(fetch).not.toHaveBeenCalled()
 expect(localStorage.getItem('harness.drafts.v1')).toBe(raw)
})

it('round trips the actual current producer including nested resources and all draft selections',async()=>{
 vi.resetModules();const values=new Map<string,string>(),write=vi.fn((key:string,value:string)=>values.set(key,value))
 vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)??null,setItem:write})
 const {useDraftStore}=await import('../drafts')
 const store=useDraftStore.getState(),attachment={localId:'local',attachmentId:'a'.repeat(64),name:'Original 原文.txt',size:3,mediaType:'text/plain',isImage:false}
 store.setText('fixture','User draft 原文');store.updateAttachments('fixture',()=>[attachment])
 store.setGoalCreation('fixture',{objective:'目标原文',rounds:'3',budget:'200'})
 store.setInlineEdit('fixture','7','Edited 原文');store.setEditOrigin('fixture',{sessionId:'fixture',sourceSeq:7})
 store.setExecutionMode('fixture','goal');store.setAgentSelection('fixture',{name:'Original Agent',agent_type:'team',display_name:'原始名称'})
 store.setTeamAddress('fixture',{team:'Original team',selectionRevision:2,members:['member'],identities:{member:'b'.repeat(64)}})
 store.rememberSubmission('fixture',{id:'request',text:'Original submission 原文',attachments:[{attachmentId:attachment.attachmentId,name:attachment.name,size:3,mediaType:attachment.mediaType,isImage:false}],action:'send',status:'unknown',sendOnReady:false,error:{template:{key:'ui.common.feedbackPair',params:{first:{key:'ui.http.invalidResponse'},second:'External 原文'}}}})
 const original=values.get('harness.drafts.v1')!
 expect(validPersistedDrafts(original)).toBe(true)
 await useDraftStore.persist.rehydrate()
 expect(useDraftStore.getState().bySession.fixture.text).toBe('User draft 原文')
 expect(useDraftStore.getState().submissions.fixture[0]!.status).toBe('unknown')
 expect(useDraftStore.getState().bySession.fixture.teamAddress?.members).toEqual(['member'])
 expect(typeof useDraftStore.getState().restoreSubmission).toBe('function')
})

it('validates nested field types, resource templates and nullable values without mutating input',()=>{
 const sample={state:{bySession:{fixture:{text:'original',attachments:[]}},submissions:{}},version:0}
 for(const extra of [{executionMode:'legacy'},{inlineEdits:{one:3}},{teamAddress:null},{goalCreation:{objective:'original'}},{editOrigin:{sessionId:'fixture',sourceSeq:true}},{attachments:[{localId:'one',name:'original',mediaType:'text/plain',size:-1,isImage:false}]}]){
  const value={...sample,state:{...sample.state,bySession:{fixture:{...sample.state.bySession.fixture,...extra}}}}
  const raw=JSON.stringify(value);expect(validPersistedDrafts(raw)).toBe(false);expect(JSON.stringify(value)).toBe(raw)
 }
})


it('keeps damaged disk data protected when an unavailable storage temporarily falls back to memory',async()=>{
 const {createBrowserDraftStorage}=await import('@/lib/browserDraftStorage')
 let available=true
 const write=vi.fn(),failed=vi.fn(),raw='Original damaged data'
 const storage={getItem:()=>raw,setItem:write} as unknown as Storage
 const adapter=createBrowserDraftStorage(()=>available?storage:undefined,failed,validPersistedDrafts)
 expect(adapter.getItem('draft')).toBeNull()
 adapter.setItem('draft',JSON.stringify(empty()))
 available=false
 expect(adapter.getItem('draft')).toBe(JSON.stringify(empty()))
 available=true
 adapter.setItem('draft',JSON.stringify(empty()))
 expect(write).not.toHaveBeenCalled();expect(storage.getItem('draft')).toBe(raw)
})


it('accepts deeply nested current resource templates and rejects unknown resource identities',()=>{
 let error: unknown = 'External 原文'
 for(let index=0;index<30;index++)error={template:{key:'ui.common.feedbackPair',params:{first:{key:'ui.http.invalidResponse'},second:error}}}
 const submission={id:'request',text:'Original 原文',attachments:[],action:'send',status:'rejected',error}
 const snapshot={state:{bySession:{},submissions:{fixture:[submission]}},version:0}
 expect(validPersistedDrafts(JSON.stringify(snapshot))).toBe(true)
 submission.error={key:'ui.unknown_fixture_key'}
 expect(validPersistedDrafts(JSON.stringify(snapshot))).toBe(false)
})


it('transparently isolates invalid originals and persists current drafts without replaying requests',async()=>{
 vi.resetModules()
 const original='Original invalid bytes 原文',values=new Map([['harness.drafts.v1',original]])
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch)
 vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value),removeItem:(key:string)=>values.delete(key)})
 const {useDraftStore}=await import('../drafts')
 await Promise.resolve()
 expect(useDraftStore.getState().storageError).toBeNull()
 expect([...values].filter(([key])=>key.startsWith('harness.drafts.v1.invalid.')).map(([,value])=>value)).toEqual([original])
 useDraftStore.getState().setText('fixture','Current input 原文')
 expect(validPersistedDrafts(values.get('harness.drafts.v1')!)).toBe(true)
 await useDraftStore.persist.rehydrate()
 expect(useDraftStore.getState().bySession.fixture.text).toBe('Current input 原文')
 expect(fetch).not.toHaveBeenCalled()
})
