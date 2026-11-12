import {afterEach,beforeEach,expect,test,vi} from 'vitest'
import {api,type ApiFailure} from '@/lib/api'
import {ResourceError} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import {setLocale,t} from '@/i18n'
import type {ResourceText,SessionDetail} from '@/types'
import {modelSaveKey,useAppStore} from '../app'
const rejected=(status=403):ApiFailure=>({ok:false,status,code:status===403?'access/denied':'network',message:'Old snapshot',copy:{key:'ui.http.invalidResponse'}})
const detail=(effort='low'):SessionDetail=>({header:{id:'owner',metadata:{reasoning_effort:effort}},events:[],messages:[],usage:{},stats:{},todos:[],timeline:[]})
beforeEach(()=>{setLocale('en');useAppStore.setState({activeSessionId:'owner',sessions:[{id:'owner',model:'old',reasoning_effort:'low',metadata:{reasoning_effort:'low'}},{id:'other',model:'old',metadata:{}}],models:[{id:'old',label:'Original old',provider:'fixture',active:true},{id:'new',label:'Original new',provider:'fixture'}],defaultModel:'old',modelVisibility:{},modelVisibilitySaves:{},reasoningSaves:{},modelSaves:{},defaultModelSave:null,toasts:[]})})
afterEach(()=>{vi.restoreAllMocks();setLocale('zh-CN')})
function language(copy:ResourceText|null|undefined,key:string,params?:()=>Record<string,string|number>){
 if(!copy)throw Error('Expected retained feedback')
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t(key,params?.()))}
}
test('definitive default model refusal retains resources and restores the previous choice',async()=>{
 const write=vi.spyOn(api,'updateSettings').mockResolvedValue(rejected()),read=vi.spyOn(api,'models')
 const result=await useAppStore.getState().setDefaultModel('new')
 expect(result).toMatchObject({status:403,code:'access/denied'})
 language(useAppStore.getState().defaultModelSave?.error,'ui.http.invalidResponse')
 const toast=useAppStore.getState().toasts[0];language(toast.title,'ui.store.app.7d58f03b954d');language(toast.description,'ui.http.invalidResponse')
 expect(useAppStore.getState().defaultModel).toBe('old');expect(write).toHaveBeenCalledTimes(1);expect(read).not.toHaveBeenCalled()
})
test('duplicate default saves preserve the busy resource and never send a second write',async()=>{
 let finish!:(value:Awaited<ReturnType<typeof api.updateSettings>>)=>void
 const write=vi.spyOn(api,'updateSettings').mockImplementation(()=>new Promise(resolve=>{finish=resolve}))
 const first=useAppStore.getState().setDefaultModel('new')
 const busy=await useAppStore.getState().setDefaultModel('another')
 expect(busy).toMatchObject({code:'model/busy',status:409});language(busy?.copy,'ui.store.app.1504ffb63d4d')
 expect(write).toHaveBeenCalledTimes(1);finish(rejected());await first
 expect(useAppStore.getState().modelSaves[modelSaveKey(null)]).toBeUndefined()
})
test('mismatched acknowledgement remains unknown after one write and one failed verification',async()=>{
 const write=vi.spyOn(api,'updateSettings').mockResolvedValue({ok:true,data:{settings:{default_model:'other'},rejected:[]}})
 const read=vi.spyOn(api,'models').mockResolvedValue(rejected(503))
 const result=await useAppStore.getState().setDefaultModel('new')
 expect(result).toMatchObject({code:'model/ack-mismatch',status:0});language(result?.copy,'ui.store.app.c2c3ae25a3d5')
 expect(useAppStore.getState().defaultModelSave?.unconfirmed).toBe(true);expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
})
test('a read receipt confirming the intended default completes without replaying the write',async()=>{
 const write=vi.spyOn(api,'updateSettings').mockResolvedValue(rejected(0)),read=vi.spyOn(api,'models').mockResolvedValue({ok:true,data:{models:[],active:'new',providers:[]}})
 expect(await useAppStore.getState().setDefaultModel('new')).toBeNull()
 expect(useAppStore.getState()).toMatchObject({defaultModel:'new',defaultModelSave:null,toasts:[]});expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
})
test('visibility acknowledgement mismatch retains current preference and its resource',async()=>{
 const write=vi.spyOn(api,'updateSettings').mockResolvedValue({ok:true,data:{settings:{},rejected:[]}}),read=vi.spyOn(api,'settings').mockResolvedValue({ok:true,data:{settings:{model_visibility:{new:true}}}})
 await useAppStore.getState().setModelVisibility('new',false)
 language(useAppStore.getState().modelVisibilitySaves.new.error,'ui.store.app.3a29146bc215')
 expect(useAppStore.getState().modelVisibilitySaves.new.unconfirmed).toBe(false);expect(useAppStore.getState().modelVisibility.new).toBe(true)
 expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
})
test('reasoning verification keeps both the receipt notice and its transport cause live',async()=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue(rejected(0)),read=vi.spyOn(api,'getSession').mockResolvedValue({ok:true,data:detail()})
 await useAppStore.getState().setReasoningEffort('high')
 language(useAppStore.getState().reasoningSaves.owner.error,'ui.store.app.340d62606aa1',()=>({p0:t('ui.http.invalidResponse')}))
 expect(useAppStore.getState().reasoningSaves.owner.unconfirmed).toBe(false);expect(useAppStore.getState().sessions[0].reasoning_effort).toBe('low')
 expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
})
test('reasoning writes remain serial and the latest intent stays with its original conversation',async()=>{
 let finish!:(value:Awaited<ReturnType<typeof api.updateSession>>)=>void
 const write=vi.spyOn(api,'updateSession').mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve})).mockResolvedValueOnce({ok:true,data:{session:{id:'owner',metadata:{reasoning_effort:'high'}}}})
 const first=useAppStore.getState().setReasoningEffort('medium'),second=useAppStore.getState().setReasoningEffort('high')
 await Promise.resolve();expect(write).toHaveBeenCalledTimes(1)
 useAppStore.setState({activeSessionId:'other'});finish(rejected());await Promise.all([first,second])
 expect(write.mock.calls.map(call=>call[1])).toEqual([{reasoning_effort:'medium'},{reasoning_effort:'high'}])
 expect(useAppStore.getState().sessions.find(row=>row.id==='owner')?.reasoning_effort).toBe('high')
 expect(useAppStore.getState().reasoningSaves.owner).toMatchObject({pending:false,error:null});expect(useAppStore.getState().reasoningSaves.other).toBeUndefined()
})
test('nested product exceptions retain their resources through the default save failure result',async()=>{
 const copy:ResourceText={template:{key:'ui.extensions.unknownResult',params:{reason:{key:'ui.http.invalidResponse'}}}}
 const write=vi.spyOn(api,'updateSettings').mockRejectedValue(new ResourceError(copy))
 const result=await useAppStore.getState().setDefaultModel('new')
 expect(result).toMatchObject({code:'model/unconfirmed',status:0})
 language(result?.copy,'ui.extensions.unknownResult',()=>({reason:t('ui.http.invalidResponse')}))
 expect(write).toHaveBeenCalledTimes(1)
})
test('session model refusals preserve the copy and cannot change another conversation',async()=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue(rejected())
 const result=await useAppStore.getState().setSessionModel('owner','new')
 language(result?.copy,'ui.http.invalidResponse')
 expect(useAppStore.getState().sessions.map(row=>row.model)).toEqual(['old','old']);expect(write).toHaveBeenCalledTimes(1)
})

for(const code of ['response/invalid-json','response/invalid-schema']){
 test(code+' in a successful HTTP status checks the original model write instead of treating it as rejected',async()=>{
  const failure={...rejected(200),code}
  const write=vi.spyOn(api,'updateSettings').mockResolvedValue(failure),read=vi.spyOn(api,'models').mockResolvedValue({ok:true,data:{models:[],active:'new',providers:[]}})
  expect(await useAppStore.getState().setDefaultModel('new')).toBeNull()
  expect(useAppStore.getState()).toMatchObject({defaultModel:'new',defaultModelSave:null,toasts:[]})
  expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
 })
}
test('malformed successful reasoning receipt reads state once without replaying the setting',async()=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue({...rejected(200),code:'response/invalid-json'}),read=vi.spyOn(api,'getSession').mockResolvedValue({ok:true,data:detail()})
 await useAppStore.getState().setReasoningEffort('high')
 language(useAppStore.getState().reasoningSaves.owner.error,'ui.store.app.340d62606aa1',()=>({p0:t('ui.http.invalidResponse')}))
 expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1);expect(useAppStore.getState().reasoningSaves.owner.unconfirmed).toBe(false)
})
test('explicit persistence failure remains a definitive refusal despite its server status',async()=>{
 const write=vi.spyOn(api,'updateSettings').mockResolvedValue({...rejected(500),code:'settings/persist-failed'}),read=vi.spyOn(api,'models')
 expect(await useAppStore.getState().setDefaultModel('new')).toMatchObject({code:'settings/persist-failed'})
 expect(useAppStore.getState().defaultModelSave?.unconfirmed).toBe(false);expect(read).not.toHaveBeenCalled();expect(write).toHaveBeenCalledTimes(1)
})

test.each(['wrong-owner','wrong-model','missing-model'] as const)('session model %s receipt requires one confirming read',async mode=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue({ok:true,data:{session:{id:mode==='wrong-owner'?'other':'owner',metadata:mode==='missing-model'?{}:{model:mode==='wrong-model'?'old':'new'}}}})
 const read=vi.spyOn(api,'getSession').mockResolvedValue({ok:true,data:{...detail(),header:{id:'owner',metadata:{model:'new'}}}})
 expect(await useAppStore.getState().setSessionModel('owner','new')).toBeNull()
 expect(useAppStore.getState().sessions.map(row=>row.model)).toEqual(['new','old'])
 expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
})
test('another conversation cannot confirm an unknown model save',async()=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue(rejected(0))
 const read=vi.spyOn(api,'getSession').mockResolvedValue({ok:true,data:{...detail(),header:{id:'other',metadata:{model:'new'}}}})
 expect(await useAppStore.getState().setSessionModel('owner','new')).toMatchObject({code:'network'})
 expect(useAppStore.getState().sessions.map(row=>row.model)).toEqual(['old','old'])
 expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
})
test.each(['new',null])('matching model receipt %s confirms without another read',async model=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue({ok:true,data:{session:{id:'owner',metadata:{model:model??''}}}})
 const read=vi.spyOn(api,'getSession')
 expect(await useAppStore.getState().setSessionModel('owner',model)).toBeNull()
 expect(useAppStore.getState().sessions[0].model).toBe(model)
 expect(write).toHaveBeenCalledTimes(1);expect(read).not.toHaveBeenCalled()
})

test('unconfirmed session model acknowledgement keeps a bilingual resource without replay',async()=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue({ok:true,data:{session:{id:'other',metadata:{model:'new'}}}})
 const read=vi.spyOn(api,'getSession').mockResolvedValue(rejected(503))
 const result=await useAppStore.getState().setSessionModel('owner','new')
 expect(result).toMatchObject({code:'model/ack-mismatch',status:0})
 language(result?.copy,'ui.modelSave.sessionUnconfirmed')
 expect(useAppStore.getState().sessions.map(row=>row.model)).toEqual(['old','old'])
 expect(write).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1)
})

test('clearing a conversation model after a refusal retains the original override',async()=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue(rejected()),read=vi.spyOn(api,'getSession')
 expect(await useAppStore.getState().setSessionModel('owner',null)).toMatchObject({status:403})
 expect(write).toHaveBeenCalledWith('owner',{model:null});expect(read).not.toHaveBeenCalled()
 expect(useAppStore.getState().sessions.map(row=>row.model)).toEqual(['old','old'])
})

test.each(['confirmed','mismatch','foreign','unavailable'] as const)('unknown conversation model reset %s checks the original conversation without replay',async mode=>{
 const write=vi.spyOn(api,'updateSession').mockResolvedValue({...rejected(200),code:'response/invalid-json'})
 const read=vi.spyOn(api,'getSession').mockResolvedValue(mode==='unavailable'?rejected(503):{ok:true,data:{...detail(),header:{id:mode==='foreign'?'other':'owner',metadata:{model:mode==='mismatch'?'actual':''}}}})
 const result=await useAppStore.getState().setSessionModel('owner',null)
 expect(result===null).toBe(mode==='confirmed')
 expect(useAppStore.getState().sessions.map(row=>row.model)).toEqual([mode==='confirmed'?null:mode==='mismatch'?'actual':'old','old'])
 expect(write).toHaveBeenCalledWith('owner',{model:null});expect(write).toHaveBeenCalledTimes(1)
 expect(read).toHaveBeenCalledWith('owner','conversation-window');expect(read).toHaveBeenCalledTimes(1)
})

test('conversation model reset owns its lock and receipt after a conversation switch',async()=>{
 let finish!:(value:Awaited<ReturnType<typeof api.updateSession>>)=>void
 const write=vi.spyOn(api,'updateSession').mockImplementation(()=>new Promise(resolve=>{finish=resolve}))
 const saving=useAppStore.getState().setSessionModel('owner',null)
 expect(useAppStore.getState().sessions[0].model).toBeNull()
 expect(await useAppStore.getState().setSessionModel('owner','new')).toMatchObject({code:'model/busy'})
 useAppStore.setState({activeSessionId:'other'})
 finish({ok:true,data:{session:{id:'owner',metadata:{model:''}}}});await saving
 expect(useAppStore.getState().sessions.map(row=>row.model)).toEqual([null,'old'])
 expect(useAppStore.getState().modelSaves[modelSaveKey('owner')]).toBeUndefined();expect(write).toHaveBeenCalledTimes(1)
})
