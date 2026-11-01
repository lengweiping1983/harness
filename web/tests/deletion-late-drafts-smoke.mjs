import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ProjectSidebar} from '/src/components/sidebar/ProjectSidebar.tsx'
import {Composer} from '/src/components/chat/Composer.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {useQueueEditStore} from '/src/store/queueEdits.ts'
import {useGitDraftStore,gitDraftScope} from '/src/store/gitDrafts.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
useAppStore.setState({sessions:[{id:'owner',title:'Owner 原文',cwd:'/fixture'},{id:'other',title:'Other 原文',cwd:'/other'}],activeSessionId:'owner',workspaces:[],toasts:[]})
useDraftStore.getState().setText('owner','Original draft 原文')
useGitDraftStore.getState().setMessage(gitDraftScope('owner','/fixture'),'Original commit 原文')
window.later=kind=>{if(kind==='git')useGitDraftStore.getState().setMessage(gitDraftScope('owner','/fixture'),'Later commit 原文');if(kind==='queue')useQueueEditStore.getState().setEdit('owner',{entry:{kind:'runs',id:'run',text:'Original queue 原文',edit_version:'a'.repeat(64)},draft:'Later queue 原文'})}
window.snapshot=()=>({draft:useDraftStore.getState().bySession.owner,other:useDraftStore.getState().bySession.other,queue:useQueueEditStore.getState().bySession.owner,git:useGitDraftStore.getState().byScope,toast:useAppStore.getState().toasts[0]})
const noop=()=>{}
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement(ProjectSidebar),React.createElement(Composer,{running:false,queued:0,planMode:false,connection:'connected',onSend:()=>{throw Error('Unexpected send')},onCancel:noop,onTogglePlan:noop}),React.createElement(Toaster)))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['text','git','queue','unchanged','other']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  let held,entered;const received=new Promise(resolve=>{entered=resolve});let writes=0
  const errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept())
  await page.route('**/api/v1/**',route=>{
   const method=route.request().method();requests.push(method)
   if(method==='GET')return route.fulfill({status:503,json:{code:'fixture/offline',error:'Original diagnostic'}})
   assert.equal(method,'DELETE');writes++;held=route;entered()
  })
  await page.goto(server.resolvedUrls.local[0]+'__deletion-late-drafts')
  await page.getByRole('button',{name:'Owner 原文',exact:true}).click({button:'right'})
  await page.getByRole('menuitem',{name:await page.evaluate(()=>window.copy('ui.components.sidebar.ProjectSidebar.d51250d14a51')),exact:true}).click()
  await received
  if(view==='text')await page.locator('textarea').fill('Later draft 原文')
  if(view==='git'||view==='queue')await page.evaluate(view=>window.later(view),view)
  if(view==='other'){await page.getByRole('button',{name:'Other 原文',exact:true}).click();await page.locator('textarea').fill('Other later draft 原文')}
  await held.fulfill({json:{deleted:'owner'}})
  const retained=['text','git','queue'].includes(view)
  await page.waitForFunction(()=>window.snapshot().toast!==undefined)
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   const expected=await page.evaluate(retained=>window.copy(retained?'ui.session.retainedLocalDrafts':'ui.store.app.495e7952925d'),retained)
   await page.waitForFunction(expected=>document.body.textContent.includes(expected),expected)
   const state=await page.evaluate(()=>window.snapshot())
   assert.equal(Boolean(state.draft),retained)
   if(view==='text')assert.equal(state.draft.text,'Later draft 原文')
   if(view==='queue')assert.equal(state.queue.draft,'Later queue 原文')
   if(view==='other')assert.equal(state.other.text,'Other later draft 原文')
   assert.equal(writes,1)
  }
  await page.clock.runFor(60000)
  if(retained){
   assert.equal(await page.evaluate(()=>window.snapshot().toast?.persistent),true,'retained-content notification must not expire')
   const downloaded=page.waitForEvent('download')
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.session.retainedLocalDownload')),exact:true}).click()
   const file=await downloaded,stream=await file.createReadStream(),chunks=[]
   assert.ok(stream);for await(const chunk of stream)chunks.push(chunk)
   const payload=JSON.parse(Buffer.concat(chunks).toString('utf8'))
   assert.equal(payload.sessionId,'owner');assert.equal(payload.draft.text,view==='text'?'Later draft 原文':'Original draft 原文')
   if(view==='queue')assert.equal(payload.queueEdit.draft,'Later queue 原文')
   if(view==='git')assert.ok(Object.values(payload.git).includes('Later commit 原文'))
   assert.ok((await page.evaluate(()=>window.snapshot())).draft,'download must not erase retained data')
  }else assert.equal(await page.evaluate(()=>window.snapshot().toast),undefined,'ordinary deletion notification still expires')
  assert.equal(writes,1);assert.deepEqual(requests.filter(method=>method!=='GET'),['DELETE']);assert.deepEqual(errors,[]);await page.close();console.log(view+': delayed deletion protects scoped drafts, normal cleanup and notification lifecycle verified')
 }
},{entry:'/__deletion-late-drafts-entry.js',vite:{plugins:[{
 name:'deletion-late-drafts-fixture',resolveId(id){if(id==='/__deletion-late-drafts-entry.js')return id},load(id){if(id==='/__deletion-late-drafts-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__deletion-late-drafts',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__deletion-late-drafts','<html><body><div id="root"></div><script type="module" src="/__deletion-late-drafts-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
