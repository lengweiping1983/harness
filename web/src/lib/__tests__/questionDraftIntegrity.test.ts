import {afterEach,beforeEach,expect,it,vi} from 'vitest'
let values: Map<string,string>
beforeEach(()=>{vi.resetModules();values=new Map();vi.stubGlobal('sessionStorage',{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value),removeItem:(key:string)=>values.delete(key)})})
afterEach(()=>vi.unstubAllGlobals())
const questions=[{id:'q',question:'Original question 原文',options:[{label:'First'},{label:'Second'}]}]
const key='harness.question-draft.v1:'+JSON.stringify(['owner','form'])
const valid={schema:JSON.stringify([['q','Original question 原文',null,false,['First','Second']]]),selected:{q:['First']},text:{q:'Original answer 原文'}}
const invalids=['', 'not json','null',JSON.stringify({...valid,schema:'old schema'}),JSON.stringify({...valid,selected:[]}),JSON.stringify({...valid,text:[]}),JSON.stringify({...valid,extra:true}),JSON.stringify({...valid,selected:{q:['Unknown']}}),JSON.stringify({...valid,selected:{q:['First','First']}}),JSON.stringify({...valid,selected:{q:['First','Second']}}),JSON.stringify({...valid,text:{other:'Original'}}),JSON.stringify({...valid,text:{q:7}})]
it.each(invalids)('preserves an invalid question original through new edits and cleanup: %s',async raw=>{
 const module=await import('../questionDraft');values.set(key,raw)
 expect(module.readQuestionDraft('owner','form',questions)).toEqual({selected:{},text:{}})
 expect(module.questionDraftProtected('owner','form')).toBe(true)
 const next={selected:{q:['Second']},text:{q:'New draft 原文'}}
 expect(module.saveQuestionDraft('owner','form',questions,next)).toBe(false)
 expect(module.readQuestionDraft('owner','form',questions)).toEqual(next)
 expect(values.get(key)).toBe(raw)
 expect(module.clearQuestionDraft('owner','form')).toBe(false)
 expect(values.get(key)).toBe(raw)
 expect(module.readQuestionDraft('owner','form',questions)).toEqual({selected:{},text:{}})
})
it('restores current valid selections exactly and persists their normal cleanup',async()=>{
 const module=await import('../questionDraft');values.set(key,JSON.stringify(valid))
 expect(module.readQuestionDraft('owner','form',questions)).toEqual({selected:valid.selected,text:valid.text})
 expect(module.questionDraftProtected('owner','form')).toBe(false)
 expect(module.clearQuestionDraft('owner','form')).toBe(true);expect(values.has(key)).toBe(false)
})
it.each(['not json','null','{}','[]',JSON.stringify('x'.repeat(100001))])('preserves invalid plan feedback when editing or completing: %s',async raw=>{
 const module=await import('../planFeedbackDraft'),id='harness.plan-feedback.v1:'+JSON.stringify(['owner','plan']);values.set(id,raw)
 expect(module.readPlanFeedback('owner','plan')).toBe('');expect(module.planFeedbackProtected('owner','plan')).toBe(true)
 expect(module.savePlanFeedback('owner','plan','New feedback 原文')).toBe(false)
 expect(module.readPlanFeedback('owner','plan')).toBe('New feedback 原文');expect(values.get(id)).toBe(raw)
 module.clearPlanFeedback('owner','plan');expect(values.get(id)).toBe(raw);expect(module.readPlanFeedback('owner','plan')).toBe('')
})
it('keeps valid plan feedback and an unrelated question writable',async()=>{
 const plan=await import('../planFeedbackDraft'),module=await import('../questionDraft')
 expect(plan.savePlanFeedback('owner','plan','Original feedback 原文')).toBe(true)
 expect(plan.readPlanFeedback('owner','plan')).toBe('Original feedback 原文')
 expect(module.saveQuestionDraft('other','form',questions,{selected:{q:['First']},text:{}})).toBe(true)
 expect(module.readQuestionDraft('other','form',questions).selected).toEqual({q:['First']})
 plan.clearPlanFeedback('owner','plan');expect(plan.readPlanFeedback('owner','plan')).toBe('')
})
