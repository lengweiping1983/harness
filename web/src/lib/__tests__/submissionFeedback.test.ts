import {afterEach,beforeEach,expect,it} from 'vitest'
import {submissionErrorText} from '../submissionFeedback'
import {useDraftStore} from '@/store/drafts'
import {noticeText} from '../noticeText'
import {setLocale,t} from '@/i18n'
beforeEach(()=>useDraftStore.setState({bySession:{},submissions:{}}))
afterEach(()=>setLocale('zh-CN'))
it.each(['attachment/invalid-storage','attachment/invalid-refs','attachment/unavailable','attachment/materialize-failed'])('keeps %s rejection in the original submission with a live resource',code=>{
 useDraftStore.getState().rememberSubmission('owner',{id:'original',text:'User 原文',attachments:[],action:'send',status:'sending',sendOnReady:true})
 useDraftStore.getState().settleSubmission('owner','original',submissionErrorText({code,message:'Host snapshot 原文'}))
 const item=useDraftStore.getState().submissions.owner![0]!
 expect(item.status).toBe('rejected');expect(item.id).toBe('original');expect(item.text).toBe('User 原文')
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(item.error??'')).toBe(t('ui.collaboration.error.'+code,{code}))}
})
it.each(['',undefined,null,{},'   '])('an empty or invalid error body cannot settle an unsent submission as success: %j',message=>{
 useDraftStore.getState().rememberSubmission('owner',{id:'original',text:'User 原文',attachments:[],action:'send',status:'sending'})
 useDraftStore.getState().settleSubmission('owner','original',submissionErrorText({message}))
 expect(useDraftStore.getState().submissions.owner).toHaveLength(1)
 expect(useDraftStore.getState().submissions.owner![0]!.status).toBe('rejected')
 expect(useDraftStore.getState().submissions.owner![0]!.error).toEqual({key:'ui.hooks.useSessionSocket.9f22a898502c'})
})
it('keeps third-party text exactly and does not translate by matching its wording',()=>{
 const text='Provider diagnostic 原文'
 expect(submissionErrorText({code:'fixture/external',message:text})).toBe(text)
 expect(submissionErrorText({error:text,message:''})).toBe(text)
})

it('an explicitly empty rejection cannot be mistaken for an omitted success argument',()=>{
 useDraftStore.getState().rememberSubmission('owner',{id:'original',text:'User 原文',attachments:[],action:'send',status:'sending'})
 useDraftStore.getState().settleSubmission('owner','original','')
 expect(useDraftStore.getState().submissions.owner![0]!.status).toBe('rejected')
 useDraftStore.getState().settleSubmission('owner','original')
 expect(useDraftStore.getState().submissions.owner).toEqual([])
})
