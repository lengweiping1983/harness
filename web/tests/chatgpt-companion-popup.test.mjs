import assert from 'node:assert/strict'
import test from 'node:test'
import {readFile} from 'node:fs/promises'

test('the same popup renders randomized timing help in English, Chinese and English without starting checks',{timeout:2000},async t=>{
 const elements=new Map()
 const element=id=>{
  if(!elements.has(id))elements.set(id,{
   dataset:{},value:'',textContent:'',type:'password',handlers:{},
   addEventListener(type,handler){this.handlers[type]=handler},
   setAttribute(){},querySelectorAll(){return []},closest(){return this},
  })
  return elements.get(id)
 }
 const timing=element('timing');timing.dataset.key='timing'
 let text='',rendered
 Object.defineProperty(timing,'textContent',{
  get:()=>text,set:value=>{text=value;rendered?.()},
 })
 const document={documentElement:{lang:''},getElementById:element,
  querySelectorAll:selector=>selector==='[data-key]'?[timing]:[]}
 let controls=0,writes=0
 const chrome={storage:{local:{get:async()=>({settings:{language:'en'}}),set:async()=>{writes++}},onChanged:{addListener(){}}},
  runtime:{getManifest:()=>({version:'1.0.0'}),sendMessage:async()=>{controls++;return {ok:true}}}}
 for(const [key,value] of Object.entries({document,chrome})){
  const previous=Object.getOwnPropertyDescriptor(globalThis,key)
  Object.defineProperty(globalThis,key,{configurable:true,writable:true,value})
  t.after(()=>{if(previous)Object.defineProperty(globalThis,key,previous);else delete globalThis[key]})
 }
 t.mock.method(globalThis,'fetch',async path=>({json:async()=>JSON.parse(await readFile(
  new URL('../../extensions/chatgpt-companion/'+path,import.meta.url),'utf8'))}))
 await import('../../extensions/chatgpt-companion/popup.mjs')
 for(const language of ['en','zh_CN','en']){
  if(element('language').value!==language){
   const translated=new Promise(resolve=>{rendered=resolve})
   element('language').value=language
   element('language').handlers.change({target:element('language')})
   await translated
  }
  const messages=JSON.parse(await readFile(new URL(
   `../../extensions/chatgpt-companion/_locales/${language}/messages.json`,import.meta.url),'utf8'))
  assert.equal(timing.textContent,messages.timing.message)
  assert.match(timing.textContent,/120[–～]180/)
  assert.match(timing.textContent,language==='en'?/randomized/:/重新随机/)
  assert.equal(document.documentElement.lang,language==='en'?'en':'zh-CN')
 }
 assert.equal(controls,0)
 assert.equal(writes,2)
})
