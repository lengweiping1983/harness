import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {WorkspacePicker} from '/src/components/overlays/WorkspacePicker.tsx'
import {WorktreeEnvironment} from '/src/components/overlays/WorktreeEnvironment.tsx'
import {TerminalWorkbench} from '/src/components/inspector/TerminalWorkbench.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;window.actions=0;setLocale('en')
const view=new URLSearchParams(location.search).get('view')
useAppStore.setState({workspacePickerOpen:view==='workspace',activeSessionId:'fixture',workspace:{root:'/fixture'}})
if(view.startsWith('toast'))useAppStore.getState().toast({title:{key:'ui.components.overlays.WorkspacePicker.51ccf18f43ba'},description:{template:{key:'ui.extensions.unknownResult',params:{reason:{key:'ui.http.invalidResponse'}}}},tone:'danger',duration:8000,action:{label:{key:'ui.changes.retry'},run:()=>{window.actions++}}})
const content=view==='workspace'?React.createElement(WorkspacePicker):view==='environment'?React.createElement(WorktreeEnvironment,{id:'worktree',sessionId:'fixture',defaultOpen:true}):view.startsWith('toast')?React.createElement(Toaster):React.createElement(TerminalWorkbench,{sessionId:view==='terminal-project'?null:'fixture'})
createRoot(document.getElementById('root')).render(content)
`
await browserScenario(async({server,browser})=>{
 for(const view of ['workspace','environment','terminal-load','terminal-project','toast-action','toast-timer']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{requests.push(route.request().method());return route.fulfill({status:200,body:'not json',contentType:'application/json'})})
  await page.goto(server.resolvedUrls.local[0]+'__terminal-workspace-error?view='+view)
  const expected=()=>page.evaluate(view=>window.copy(view.startsWith('toast')?'ui.components.overlays.WorkspacePicker.51ccf18f43ba':'ui.http.invalidResponse'),view)
  await page.getByText(await expected(),{exact:true}).waitFor()
  const before=requests.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await page.getByText(await expected(),{exact:true}).waitFor();assert.equal(requests.length,before)
   if(view.startsWith('toast')){
    const strings=await page.evaluate(()=>({description:window.copy('ui.extensions.unknownResult',{reason:window.copy('ui.http.invalidResponse')}),dismiss:window.copy('ui.components.overlays.CommandPalette.58ce29e73319',{p0:window.copy('ui.components.overlays.WorkspacePicker.51ccf18f43ba')}),action:window.copy('ui.changes.retry')}))
    await page.getByText(strings.description,{exact:true}).waitFor();await page.getByRole('button',{name:strings.dismiss,exact:true}).waitFor();await page.getByRole('button',{name:strings.action,exact:true}).waitFor()
   }
  }
  if(view==='toast-action'){
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.changes.retry')),exact:true}).click()
   assert.equal(await page.evaluate(()=>window.actions),1);assert.equal(await page.getByRole('alert').count(),0)
  }
  if(view==='toast-timer'){
   await page.clock.runFor(4000);await page.evaluate(()=>window.locale('zh-CN'));await page.getByText(await expected(),{exact:true}).waitFor()
   await page.clock.runFor(3999);assert.equal(await page.getByRole('alert').count(),1)
   await page.clock.runFor(1);await page.getByRole('alert').waitFor({state:'detached'})
  }
  assert.equal(requests.filter(method=>method!=='GET').length,view==='terminal-project'?1:0)
  assert.deepEqual(errors,[],view);await page.close();console.log(view+': feedback language and operation ownership remain stable')
 }
},{entry:'/__terminal-workspace-error-entry.js',vite:{plugins:[{
 name:'terminal-workspace-error-fixture',resolveId(id){if(id==='/__terminal-workspace-error-entry.js')return id},load(id){if(id==='/__terminal-workspace-error-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__terminal-workspace-error',async(_req,res,next)=>{
  try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__terminal-workspace-error','<html><body><div id="root"></div><script type="module" src="/__terminal-workspace-error-entry.js"></script></body></html>'))}catch(error){next(error)}
 })},
}]}})
