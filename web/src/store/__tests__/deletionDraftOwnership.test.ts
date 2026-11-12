import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {api} from '@/lib/api'
import {useAppStore} from '../app'
import {useDraftStore} from '../drafts'
import {useQueueEditStore} from '../queueEdits'
import {useGitDraftStore,gitDraftScope} from '../gitDrafts'
const scope=gitDraftScope('owner','/fixture')
const entry={kind:'runs' as const,id:'run',text:'Original queue 原文',edit_version:'a'.repeat(64)}
beforeEach(()=>{
 useDraftStore.setState({bySession:{},submissions:{}});useQueueEditStore.setState({bySession:{},withdrawals:{}});useGitDraftStore.setState({byScope:{}})
 useDraftStore.getState().setText('owner','Original draft 原文')
 useGitDraftStore.getState().setMessage(scope,'Original commit 原文')
 useAppStore.setState({sessions:[{id:'owner',cwd:'/fixture'},{id:'other',cwd:'/other'}],activeSessionId:'owner',toasts:[]})
})
afterEach(()=>vi.restoreAllMocks())
it.each(['text','attachment','submission','queue','withdrawal','git'] as const)('deletion cannot discard a later %s update',async kind=>{
 let finish!:(value:Awaited<ReturnType<typeof api.deleteSession>>)=>void
 const remove=vi.spyOn(api,'deleteSession').mockReturnValue(new Promise(resolve=>{finish=resolve}))
 const pending=useAppStore.getState().removeSession('owner')
 if(kind==='text')useDraftStore.getState().setText('owner','Later draft 原文')
 if(kind==='attachment')useDraftStore.getState().updateAttachments('owner',()=>[{localId:'later',name:'Later 原文.txt',mediaType:'text/plain',size:1,isImage:false,status:'pending'}])
 if(kind==='submission')useDraftStore.getState().rememberSubmission('owner',{id:'later',text:'Later submission 原文',attachments:[],action:'send',status:'unknown'})
 if(kind==='queue')useQueueEditStore.getState().setEdit('owner',{entry,draft:'Later queue 原文'})
 if(kind==='withdrawal')useQueueEditStore.getState().setWithdrawal('owner',entry)
 if(kind==='git')useGitDraftStore.getState().setMessage(scope,'Later commit 原文')
 finish({ok:true,data:{deleted:'owner'}});await pending
 expect(useAppStore.getState().sessions.map(row=>row.id)).toEqual(['other'])
 expect(useDraftStore.getState().bySession.owner).toBeDefined()
 expect(useGitDraftStore.getState().byScope[scope]).toBeDefined()
 if(kind==='queue')expect(useQueueEditStore.getState().bySession.owner?.draft).toBe('Later queue 原文')
 if(kind==='withdrawal')expect(useQueueEditStore.getState().withdrawals.owner).toEqual(entry)
 if(kind==='submission')expect(useDraftStore.getState().submissions.owner?.[0]?.text).toBe('Later submission 原文')
 expect(useAppStore.getState().toasts[0]).toMatchObject({persistent:true,action:{label:{key:'ui.session.retainedLocalDownload'}}})
 expect(remove).toHaveBeenCalledTimes(1)
})
it('unchanged original data is removed while another conversation edits do not block cleanup',async()=>{
 let finish!:(value:Awaited<ReturnType<typeof api.deleteSession>>)=>void
 vi.spyOn(api,'deleteSession').mockReturnValue(new Promise(resolve=>{finish=resolve}))
 const pending=useAppStore.getState().removeSession('owner')
 useDraftStore.getState().setText('other','Other draft 原文')
 useGitDraftStore.getState().setMessage(gitDraftScope('other','/other'),'Other commit 原文')
 finish({ok:true,data:{deleted:'owner'}});await pending
 expect(useDraftStore.getState().bySession.owner).toBeUndefined()
 expect(useGitDraftStore.getState().byScope[scope]).toBeUndefined()
 expect(useDraftStore.getState().bySession.other?.text).toBe('Other draft 原文')
 expect(useAppStore.getState().toasts[0]?.persistent).not.toBe(true)
})
