import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {FileOpenActions} from '/src/components/inspector/FileOpenActions.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
function Fixture(){const [path,setPath]=useState('first.txt');window.file=setPath;window.locale=setLocale;return React.createElement(FileOpenActions,{path,root:'/fixture'})}
window.label=key=>t(key)
window.saved=[]
window.showSaveFilePicker=()=>new Promise(resolve=>{window.releaseSave=()=>resolve({createWritable:async()=>new WritableStream({write:chunk=>window.saved.push(new TextDecoder().decode(chunk))})})})
setLocale('en');createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
const html='<html><body><div id="root"></div><script type="module" src="/__file-open-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
 let writes=0,release,entered
 const held=new Promise(resolve=>{entered=resolve})
 await page.route('**/api/v1/file/open',async route=>{
  writes++;entered();await new Promise(resolve=>{release=resolve})
  await route.fulfill({status:500,json:{code:'fixture/error',message:'old file failure'}})
 })
 let downloadPath
 await page.route('**/api/v1/file?*',route=>{downloadPath=new URL(route.request().url()).searchParams.get('path');return route.fulfill({contentType:'text/plain',body:'original bytes'})})
 await page.goto(server.resolvedUrls.local[0]+'__file-open')
 const open=page.locator('.file-open-main');await open.waitFor()
 await open.evaluate(element=>{element.click();element.click()});await held
 assert.equal(writes,1,'Two same-tick activations must start only one desktop action')
 await page.evaluate(()=>window.file('second.txt'))
 await page.waitForFunction(()=>!document.querySelector('.file-open-main')?.disabled)
 const response=page.waitForResponse(response=>response.url().endsWith('/file/open'))
 release();await(await response).finished()
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
 assert.equal(await page.getByRole('alert').count(),0,'Previous file failure must not appear on the new file')
 for(const language of ['zh-CN','en']){
  await page.evaluate(language=>window.locale(language),language)
  const next=new Promise(resolve=>{entered=resolve})
  await open.click();await next
  const done=page.waitForResponse(response=>response.url().endsWith('/file/open'))
  release();await(await done).finished()
  await page.getByRole('alert').waitFor()
  assert.equal(await page.getByRole('alert').innerText(),'old file failure')
  assert.equal(await open.isDisabled(),false)
 }
 assert.equal(writes,3)
 const saveLabel=await page.evaluate(()=>window.label('ui.components.inspector.FileOpenActions.9016c463977d'))
 await page.locator('.file-open-menu').click()
 await page.getByRole('menuitem',{name:saveLabel,exact:true}).click()
 await page.waitForFunction(()=>typeof window.releaseSave==='function')
 await page.evaluate(()=>window.file('third.txt'))
 await page.waitForFunction(()=>!document.querySelector('.file-open-main')?.disabled)
 await page.evaluate(()=>window.releaseSave())
 await page.waitForFunction(()=>window.saved.join('')==='original bytes')
 assert.equal(downloadPath,'second.txt','Accepted save must finish using its original source')
 assert.equal(await page.getByRole('alert').count(),0)
 assert.deepEqual(errors,[])
 console.log('File actions: synchronous duplicate lock, source replacement isolation, visible current failure and explicit retry after failure passed')
},{entry:'/__file-open-entry.js',vite:{plugins:[{name:'file-open-fixture',resolveId(id){if(id==='/__file-open-entry.js')return id},load(id){if(id==='/__file-open-entry.js')return entry},configureServer(server){server.middlewares.use('/__file-open',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__file-open',html))}catch(error){next(error)}})}}]}})
