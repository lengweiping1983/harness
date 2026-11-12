import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueuedInputEditor} from '/src/components/chat/QueuedInputEditor.tsx'
import {useQueueEditStore} from '/src/store/queueEdits.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;window.completions=0
const root=createRoot(document.getElementById('root'))
const entry={id:'original',session_id:'owner',kind:'runs',text:'Original\\n\\n---\\n本条消息附带 User text',edit_version:'a'.repeat(64),attachment_refs:[{attachmentId:'a'.repeat(64),name:'Original.txt',mediaType:'text/plain',size:5,isImage:false}]}
let generation=0
window.reset=()=>{useQueueEditStore.getState().setEdit('owner',{entry,draft:entry.text});root.render(React.createElement(QueuedInputEditor,{key:++generation,sessionId:'owner',entries:[entry],ready:true,disabled:false,onWorking:()=>{},onChanged:()=>{},onClosed:()=>window.completions++}))}
window.reset()
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(10000)
 const errors=[],writes=[];page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{
  assert.equal(route.request().method(),'PATCH')
  const body=route.request().postDataJSON();writes.push(body)
  return route.fulfill({json:{id:'original',session_id:'owner',text:body.text,edit_version:'b'.repeat(64)}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__queue-original')
 for(const dark of [false,true])for(const width of [390,1440])for(const text of ['New original\n\n---\nThis message includes User text','']){
  await page.setViewportSize({width,height:900});await page.evaluate(()=>window.reset())
  const input=page.getByRole('textbox')
  await input.waitFor();assert.equal(await input.inputValue(),'Original\n\n---\n本条消息附带 User text')
  await input.fill(text)
  const before=writes.length
  for(const language of ['en','zh-CN','en']){
   await page.evaluate(({language,dark})=>{window.locale(language);document.documentElement.classList.toggle('dark',dark)},{language,dark})
   assert.equal(await input.inputValue(),text);assert.equal(writes.length,before)
  }
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.QueuedInputEditor.756b121cc6b1')),exact:true}).click()
  await input.waitFor({state:'detached'})
  assert.equal(writes.length,before+1)
  assert.deepEqual(writes.at(-1),{text,expected_version:'a'.repeat(64)})
 }
 assert.deepEqual(errors,[]);await page.close()
 console.log('Queue editing preserves literal markers, sends only original text, permits attachment-only bodies and never writes on locale changes')
},{entry:'/__queue-original-entry.js',vite:{plugins:[{name:'queue-original-fixture',resolveId(id){if(id==='/__queue-original-entry.js')return id},load(id){if(id==='/__queue-original-entry.js')return entry},configureServer(server){server.middlewares.use('/__queue-original',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__queue-original','<html><body><div id="root"></div><script type="module" src="/__queue-original-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
