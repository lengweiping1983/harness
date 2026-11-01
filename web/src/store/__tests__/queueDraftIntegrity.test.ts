import {afterEach,expect,it,vi} from 'vitest'
import {validPersistedQueueEdits} from '@/lib/draftPersistence'
const entry={kind:'runs',id:'run',text:'Original queue 原文',edit_version:'a'.repeat(64)}
const snapshot=(value:unknown)=>JSON.stringify({state:{bySession:{fixture:value},withdrawals:{}},version:0})
afterEach(async()=>{vi.unstubAllGlobals();(await import('@/i18n')).setLocale('zh-CN')})
it.each([
 '{','null',JSON.stringify({state:{bySession:{},withdrawals:{},setEdit:'injected'},version:0}),
 snapshot({entry:{...entry,kind:'legacy'},draft:'Original 原文'}),
 snapshot({entry:{...entry,edit_version:'bad'},draft:'Original 原文'}),
 snapshot({entry:{...entry,session_id:'other'},draft:'Original 原文'}),
 snapshot({entry,draft:3}),snapshot({entry,draft:'Original',unconfirmed:{entry:{...entry,id:'other'},text:'old request'}}),
])('preserves damaged session data without hydrating operations or replaying requests: %s',async raw=>{
 vi.resetModules();const write=vi.fn(),remove=vi.fn(),fetch=vi.fn()
 vi.stubGlobal('sessionStorage',{getItem:()=>raw,setItem:write,removeItem:remove});vi.stubGlobal('fetch',fetch)
 const {useQueueEditStore}=await import('../queueEdits');await Promise.resolve()
 expect(typeof useQueueEditStore.getState().setEdit).toBe('function');expect(useQueueEditStore.getState().bySession).toEqual({})
 useQueueEditStore.getState().setEdit('fixture',{entry:{...entry,kind:'runs'},draft:'New edit 原文'})
 useQueueEditStore.getState().setWithdrawal('fixture',{...entry,kind:'runs'})
 await useQueueEditStore.persist.rehydrate()
 expect(useQueueEditStore.getState().bySession.fixture.draft).toBe('New edit 原文')
 expect(useQueueEditStore.getState().withdrawals.fixture.id).toBe('run')
 const {setLocale,t}=await import('@/i18n'),{noticeText}=await import('@/lib/noticeText')
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(useQueueEditStore.getState().storageError??'')).toBe(t('ui.queueDrafts.invalidStorage'))}
 useQueueEditStore.persist.clearStorage()
 expect(write).not.toHaveBeenCalled();expect(remove).not.toHaveBeenCalled();expect(fetch).not.toHaveBeenCalled()
 expect(sessionStorage.getItem('harness.queue-edits.v1')).toBe(raw)
})
it('accepts current run metadata and preserves unknown writes for read-only confirmation',async()=>{
 vi.resetModules();const values=new Map<string,string>()
 vi.stubGlobal('sessionStorage',{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value)})
 const {useQueueEditStore}=await import('../queueEdits')
 const row={...entry,kind:'runs' as const,session_id:'fixture',status:'queued',goal_id:null,created_at:1,started_at:null,result:null,error:null,
  requester:{subject:'fixture',display_name:'Original 用户'},attachment_refs:[{attachmentId:'a'.repeat(64),name:'Original 原文.txt',mediaType:'text/plain',size:3,isImage:false}],
  team_route:{team:'Original team',revision:'revision',selection_revision:3,recipients:['member'],recipient_identities:{member:'b'.repeat(64)}}}
 useQueueEditStore.getState().setEdit('fixture',{entry:row,draft:'New edit 原文',unconfirmed:{entry:row,text:'Accepted write 原文'}})
 useQueueEditStore.getState().setWithdrawal('fixture',row)
 expect(validPersistedQueueEdits(values.get('harness.queue-edits.v1')!)).toBe(true)
 await useQueueEditStore.persist.rehydrate()
 expect(useQueueEditStore.getState().bySession.fixture.unconfirmed?.text).toBe('Accepted write 原文')
 expect(useQueueEditStore.getState().withdrawals.fixture).toEqual(row)
 expect(typeof useQueueEditStore.getState().setWithdrawal).toBe('function')
})
it('rejects invalid withdrawals, route identities, requester and attachment shapes',()=>{
 for(const change of [{session_id:'other'},{attachments:true},{attachments:1},{unknownMetadata:'unrecognized'},{requester:{subject:'one'}},{attachment_refs:[null]},
  {team_route:{team:'team',revision:'revision',selection_revision:true,recipients:[],recipient_identities:{}}}]){
  expect(validPersistedQueueEdits(JSON.stringify({state:{bySession:{},withdrawals:{fixture:{...entry,...change}}},version:0}))).toBe(false)
 }
})
