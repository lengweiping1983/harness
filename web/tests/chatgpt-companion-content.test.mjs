import assert from 'node:assert/strict'
import test from 'node:test'
import {inspectPage} from '../../extensions/chatgpt-companion/page.mjs'

test('continuous dispatch stays bound to its observed reply before and after filling the composer',async t=>{
 const url='https://chatgpt.com/c/00000000-0000-0000-0000-000000000001'
 const messages=[]
 let listener,clicks=0,changeOnInput=false
 const message=text=>({textContent:text,contains:()=>false,compareDocumentPosition:()=>4,
  getAttribute:()=>null,closest:()=>null,querySelectorAll:()=>[],cloneNode:()=>({textContent:text,querySelectorAll:()=>[]})})
 const assistant=message('Observed reply')
 const editor={value:'',focus(){},closest:()=>form,dispatchEvent(){if(changeOnInput)assistant.textContent='A newer reply'}}
 const button={disabled:false,click(){clicks++;messages.push(message(editor.value));editor.value=''}}
 const form={querySelector:()=>null,querySelectorAll:selector=>selector.startsWith('button[')?[button]:[]}
 const document={addEventListener(){},querySelectorAll:selector=>{
  if(selector.startsWith('#prompt-textarea'))return [editor]
  if(selector==='[data-message-author-role="user"]')return messages
  if(selector==='[data-message-author-role="assistant"]')return [assistant]
  return []
 }}
 const chrome={runtime:{id:'fixture',getURL:()=>new URL('../../extensions/chatgpt-companion/page.mjs',import.meta.url).href,
  onMessage:{addListener:callback=>{listener=callback}}}}
 const location=new URL(url)
 for(const [key,value] of Object.entries({document,chrome,location,InputEvent:class{}})){
  const previous=Object.getOwnPropertyDescriptor(globalThis,key)
  Object.defineProperty(globalThis,key,{configurable:true,writable:true,value})
  t.after(()=>{if(previous)Object.defineProperty(globalThis,key,previous);else delete globalThis[key]})
 }
 t.mock.method(Date,'now',()=>10000)
 await import('../../extensions/chatgpt-companion/content.js')
 const dispatch=assistantKey=>new Promise(resolve=>listener({type:'dispatch',url,prompt:'继续',assistantKey},{id:'fixture'},resolve))
 const key=inspectPage(document,url,url).assistantKey
 assert.deepEqual(await dispatch('0'.repeat(16)),{outcome:'not_sent',reason:'page_changed'})
 assert.equal(editor.value,'');assert.equal(clicks,0)
 assert.deepEqual(await dispatch(key),{outcome:'confirmed'})
 assert.equal(clicks,1)
 changeOnInput=true
 assert.deepEqual(await dispatch(key),{outcome:'unknown',reason:'user_active'})
 assert.equal(clicks,1);assert.equal(editor.value,'继续')
})
