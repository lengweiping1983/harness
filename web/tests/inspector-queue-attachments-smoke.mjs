import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const imageId='a'.repeat(64),fileId='b'.repeat(64)
const attachments=[
 {attachmentId:imageId,name:'界面草图.png',mediaType:'image/png',size:68,isImage:true},
 {attachmentId:fileId,name:'需求说明.txt',mediaType:'text/plain',size:27,isImage:false},
]
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TaskQueue} from '/src/components/inspector/TaskQueue.tsx'
import {ImageLightbox} from '/src/components/markdown/ImageLightbox.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
useAppStore.setState({activeSessionId:'owner'})
createRoot(document.getElementById('root')).render(React.createElement('main',{className:'min-h-screen bg-surface p-3 text-ink'},React.createElement(TaskQueue,{sessionId:'owner'}),React.createElement(ImageLightbox)))
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage({viewport:{width:390,height:900}}),errors=[],writes=[]
 page.setDefaultTimeout(5000)
 page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 let text='Original user text 原文',reads=0
 await page.context().route('**/api/v1/attachments/**',route=>new URL(route.request().url()).pathname.endsWith(imageId)
  ?route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2d8AAAAASUVORK5CYII=','base64')})
  :route.fulfill({contentType:'text/plain; charset=utf-8',body:'Fixture attachment contents'}))
 await page.route('**/api/v1/sessions/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(request.method()==='PATCH'){
   assert.ok(path.endsWith('/runs/with-files'))
   const body=request.postDataJSON();writes.push(body);text=body.text
   return route.fulfill({json:{id:'with-files',text,edit_version:'c'.repeat(64)}})
  }
  reads++
  if(path.endsWith('/queue'))return route.fulfill({json:{runs:[
   {id:'only-files',text:'',edit_version:'a'.repeat(64),attachment_refs:attachments},
   {id:'with-files',text,edit_version:(writes.length?'c':'b').repeat(64),attachment_refs:attachments},
   {id:'plain',text:'Text only 纯文字',edit_version:'d'.repeat(64)},
  ]}})
  if(path.endsWith('/runs'))return route.fulfill({json:{runs:[],has_more:false,next_before:null}})
  throw Error('Unexpected queue request: '+path)
 })
 await page.goto(server.resolvedUrls.local[0]+'__inspector-queue-attachments')
 const row=id=>page.locator('[data-edit-key="runs:'+id+'"]').locator('xpath=../..')
 const only=row('only-files'),mixed=row('with-files'),plain=row('plain')
 await only.getByRole('img',{name:'界面草图.png',exact:true}).waitFor()
 await only.getByRole('button',{name:'界面草图.png',exact:true}).click()
 await page.getByRole('dialog').getByRole('img',{name:'界面草图.png',exact:true}).waitFor()
 await page.waitForFunction(()=>document.querySelector('[role="dialog"] img')?.naturalWidth>0)
 await page.keyboard.press('Escape')
 await page.getByRole('dialog').waitFor({state:'detached'})
 const [file]=await Promise.all([page.waitForEvent('popup'),only.getByRole('link',{name:/需求说明.txt/}).click()])
 await file.getByText('Fixture attachment contents',{exact:true}).waitFor();await file.close()
 const matrix=async check=>{
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   await check(locale)
   assert.equal(await page.locator('body').evaluate(node=>node.scrollWidth<=innerWidth+1),true,'Queue attachments must fit the panel')
  }
 }
 const baseline=reads
 await matrix(async()=>{
  for(const row of [only,mixed]){
   await row.getByRole('img',{name:'界面草图.png',exact:true}).waitFor()
   assert.equal(await row.getByRole('link',{name:/需求说明.txt/}).getAttribute('href'),'/api/v1/attachments/'+fileId)
  }
  await only.getByText(await page.evaluate(()=>window.copy('ui.components.chat.QueuedFollowups.402c881d6d0b')),{exact:true}).waitFor()
  assert.equal(await plain.locator('img,a').count(),0)
 })
 assert.equal(reads,baseline,'Presentation changes must not reload the queue')
 await mixed.getByRole('button',{name:'Edit',exact:true}).click()
 const input=page.getByRole('textbox'),save=page.getByRole('button',{name:'Save',exact:true})
 await input.fill('')
 await matrix(async()=>{
  assert.equal(await input.inputValue(),'')
  await page.getByText(await page.evaluate(()=>window.copy('ui.components.chat.QueuedInputEditor.6f6153ce5a74')),{exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.inspector.TaskQueue.a3030bf8f16d')),exact:true}).isEnabled(),true)
  assert.equal(writes.length,0)
 })
 await save.click();await input.waitFor({state:'detached'})
 assert.deepEqual(writes,[{text:'',expected_version:'b'.repeat(64)}],'Only the original text changes; attachment references stay on the queued task')
 await mixed.getByText('Attachment message',{exact:true}).waitFor()
 await mixed.getByRole('img',{name:'界面草图.png',exact:true}).waitFor()
 await plain.getByRole('button',{name:'Edit',exact:true}).click();await input.fill('')
 assert.equal(await save.isEnabled(),false,'A text-only task must still reject an empty body')
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Inspector queue attachments: image/file previews, attachment-only rows and edits, text-only guard, en→zh→en and theme/width matrix passed')
},{entry:'/__inspector-queue-attachments-entry.js',vite:{plugins:[{
 name:'inspector-queue-attachments-fixture',resolveId(id){if(id==='/__inspector-queue-attachments-entry.js')return id},load(id){if(id==='/__inspector-queue-attachments-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__inspector-queue-attachments',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__inspector-queue-attachments','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__inspector-queue-attachments-entry.js"></script></body></html>'))}catch(error){next(error)}})},
}]}})
