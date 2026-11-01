import {expect,it} from 'vitest'
import {personalityDraftKey,readPersonalityDraft,listPersonalityDrafts,persistPersonalityDraft} from '../personalityDraft'
const draft={name:'Original name 原文',description:'Original description 原文',prompt:'Original prompt 原文',code:'systems-planner',editing:null}
it('restores current semantic personality codes and exact editing identity',()=>{
 expect(readPersonalityDraft(JSON.stringify(draft))).toEqual(draft)
 const edited={...draft,code:null,editing:{id:'a'.repeat(32),revision:'b'.repeat(64),name:'Original name 原文'}}
 expect(readPersonalityDraft(JSON.stringify(edited))).toEqual(edited)
})
it('rejects old letter codes and inherited names without rewriting storage',()=>{
 const values=new Map<string,string>()
 for(const code of ['INTJ','toString','__proto__','constructor','unknown-style']){
  const raw=JSON.stringify({...draft,code});expect(readPersonalityDraft(raw)).toBeNull();values.set(personalityDraftKey(code),raw)
 }
 const original=[...values];const storage={get length(){return values.size},key:(index:number)=>[...values.keys()][index]??null,getItem:(key:string)=>values.get(key)??null}
 expect(listPersonalityDrafts(storage).size).toBe(0);expect([...values]).toEqual(original)
 values.set(personalityDraftKey('current'),JSON.stringify(draft))
 expect([...listPersonalityDrafts(storage).values()]).toEqual([draft]);expect([...values].slice(0,original.length)).toEqual(original)
})

it('editing and cleanup preserve corrupt same-key bytes while other drafts stay writable',()=>{
 const key=personalityDraftKey('new'),other=personalityDraftKey('other')
 for(const raw of ['', 'Original broken bytes', JSON.stringify({...draft,code:'INTJ'})]){
  const values=new Map([[key,raw]])
  const storage={getItem:(name:string)=>values.get(name)??null,setItem:(name:string,value:string)=>{values.set(name,value)},removeItem:(name:string)=>{values.delete(name)}}
  expect(persistPersonalityDraft(storage,key,draft)).toBe(false)
  expect(persistPersonalityDraft(storage,key,null)).toBe(false)
  expect(values.get(key)).toBe(raw)
  expect(persistPersonalityDraft(storage,other,draft)).toBe(true)
  expect(readPersonalityDraft(values.get(other)!)).toEqual(draft)
  expect(persistPersonalityDraft(storage,other,null)).toBe(true)
  expect(values.has(other)).toBe(false);expect(values.get(key)).toBe(raw)
 }
})
it('a draft corrupted after catalog loading is checked again before writing',()=>{
 let raw:string|null=JSON.stringify(draft)
 const storage={getItem:()=>raw,setItem:(_key:string,value:string)=>{raw=value},removeItem:()=>{raw=null}}
 expect(readPersonalityDraft(storage.getItem())).toEqual(draft)
 raw='Later corrupt original'
 expect(persistPersonalityDraft(storage,personalityDraftKey('new'),{...draft,prompt:'New edit'})).toBe(false)
 expect(raw).toBe('Later corrupt original')
})
it('storage failure and invalid new values never discard the last valid draft',()=>{
 const original=JSON.stringify(draft)
 let writes=0
 const storage={getItem:()=>original,setItem:()=>{writes++;throw new Error('Original storage failure')},removeItem:()=>{throw new Error('Original storage failure')}}
 expect(persistPersonalityDraft(storage,'fixture',{...draft,code:'old-code'})).toBe(false)
 expect(writes).toBe(0)
 expect(persistPersonalityDraft(storage,'fixture',draft)).toBe(false)
 expect(writes).toBe(1)
 expect(persistPersonalityDraft(storage,'fixture',null)).toBe(false)
 expect(storage.getItem()).toBe(original)
})
