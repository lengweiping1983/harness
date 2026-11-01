import {afterEach,expect,test} from 'vitest'
import {setLocale,t} from '@/i18n'
import {ApiError} from '../http'
import {noticeText} from '../noticeText'
import {assertQuestionAnswerAccepted,questionSubmissionFeedback} from '../questionSubmission'
afterEach(()=>setLocale('zh-CN'))
for(const [status,unconfirmed] of [[0,true],[200,true],[408,true],[409,false],[500,true]] as const){
 test(`question status ${status} keeps its certainty and product resources`,()=>{
  setLocale('en')
  const saved=questionSubmissionFeedback(new ApiError({ok:false,status,code:status===409?'question/conflict':'response/invalid-json',message:'Old snapshot',copy:{key:'ui.http.invalidResponse'}}))
  expect(saved.unconfirmed).toBe(unconfirmed)
  for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(saved.title)).toBe(t(unconfirmed?'ui.lib.questionSubmission.6ead3e29ddbd':'ui.lib.questionSubmission.29e188fd3ee5'));expect(noticeText(saved.message)).toBe(t('ui.http.invalidResponse'))}
 })
}
test('a negative answer receipt retains its conflict code and explicit resource',()=>{
 let error:unknown
 try{assertQuestionAnswerAccepted({resolved:false})}catch(failure){error=failure}
 expect(error).toMatchObject({code:'question/conflict',status:409})
 const feedback=questionSubmissionFeedback(error)
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(feedback.message)).toBe(t('ui.lib.questionSubmission.b9d682b36184'))}
 expect(feedback.unconfirmed).toBe(false)
})
