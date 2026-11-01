import {afterEach,beforeEach,expect,test,vi} from 'vitest'
import {saveRecoveryDraft,readRecoveryDraft,clearSubmittedRecoveryDraft,type RecoveryEditorDraft} from '../recoveryDraft'
let values:Map<string,string>
beforeEach(()=>{values=new Map();vi.stubGlobal('sessionStorage',{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value),removeItem:(key:string)=>values.delete(key)})})
afterEach(()=>vi.unstubAllGlobals())
const original=():RecoveryEditorDraft=>({mode:'inspect',instructions:{inspect:'Original inspection 原文',continue:'Original continuation 原文'},note:'Original verification 原文',sourceVersion:'original'})
test('an inspection acknowledgement preserves the separate continuation and verification note',()=>{
 const expected=original();saveRecoveryDraft('owner','run',expected)
 expect(clearSubmittedRecoveryDraft('owner','run','inspect',expected)).toBe(true)
 expect(readRecoveryDraft('owner','run')).toEqual({...expected,mode:'continue',instructions:{continue:expected.instructions.continue}})
})
for(const change of ['instructions','note','version'] as const){
 test('late acknowledgement preserves changed '+change,()=>{
  const expected=original()
  const updated={...expected,instructions:{...expected.instructions}}
  if(change==='instructions')updated.instructions.continue='New continuation 原文'
  if(change==='note')updated.note='New verification 原文'
  if(change==='version')updated.sourceVersion='new-version'
  saveRecoveryDraft('owner','run',updated)
  expect(clearSubmittedRecoveryDraft('owner','run','continue',expected)).toBe(true)
  expect(readRecoveryDraft('owner','run')).toEqual(updated)
 })
}
test('a continuation acknowledgement clears only its original instruction and note',()=>{
 const expected=original();saveRecoveryDraft('owner','run',expected)
 expect(clearSubmittedRecoveryDraft('owner','run','continue',expected)).toBe(true)
 expect(readRecoveryDraft('owner','run')).toEqual({...expected,mode:'inspect',note:'',instructions:{inspect:expected.instructions.inspect}})
})
test('acknowledging the final unchanged instruction removes the draft',()=>{
 const expected={...original(),instructions:{inspect:'Original inspection 原文'},note:''}
 saveRecoveryDraft('owner','run',expected)
 expect(clearSubmittedRecoveryDraft('owner','run','inspect',expected)).toBe(true)
 expect(readRecoveryDraft('owner','run')).toBeNull()
})
