import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const workspace={id:'workspace',path:'/fixture',name:'Project 原文',color:'blue'}
const session={id:'target',cwd:'/fixture',title:'Conversation 原文'}
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ProjectSidebar} from '/src/components/sidebar/ProjectSidebar.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
useAppStore.setState({sessions:[${JSON.stringify(session)}],activeSessionId:'target',workspaces:[${JSON.stringify(workspace)}],activeWorkspaceId:'workspace',toasts:[]})
useDraftStore.getState().setText('target','Original draft 原文')
window.snapshot=()=>({draft:useDraftStore.getState().bySession.target?.text,sessions:useAppStore.getState().sessions.map(row=>row.id),workspaces:useAppStore.getState().workspaces.map(row=>row.id)})
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement(ProjectSidebar),React.createElement(Toaster)))
`
await browserScenario(async({server,browser})=>{
 for(const kind of ['session','workspace'])for(const absent of [false,true]){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept())
  let deletion,accepted
  const started=new Promise(resolve=>{accepted=resolve})
  await page.route('**/api/v1/**',route=>{
   const request=route.request();requests.push({method:request.method(),path:new URL(request.url()).pathname})
   if(request.method()==='DELETE'){deletion=route;accepted();return}
   return route.fulfill({json:kind==='session'?{sessions:absent?[]:[session]}:{workspaces:absent?[]:[workspace],default:'/fixture'}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__sidebar-deletion-receipt')
  if(kind==='session')await page.getByRole('button',{name:'Conversation 原文',exact:true}).click({button:'right'})
  else await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.sidebar.ProjectSidebar.ef430e30fb08')),exact:true}).first().click()
  await page.getByRole('menuitem',{name:await page.evaluate(kind=>window.copy(kind==='session'?'ui.components.sidebar.ProjectSidebar.d51250d14a51':'ui.components.sidebar.ProjectSidebar.7b749d300b7a'),kind),exact:true}).click()
  await started
  if(kind==='session')await page.getByRole('button',{name:'Conversation 原文',exact:true}).click({button:'right'})
  else await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.sidebar.ProjectSidebar.ef430e30fb08')),exact:true}).first().click()
  await page.getByRole('menuitem',{name:await page.evaluate(kind=>window.copy(kind==='session'?'ui.components.sidebar.ProjectSidebar.d51250d14a51':'ui.components.sidebar.ProjectSidebar.7b749d300b7a'),kind),exact:true}).click()
  await page.clock.runFor(32)
  assert.equal(requests.filter(request=>request.method==='DELETE').length,1,'reopened menu joins the in-flight deletion')
  await deletion.fulfill({json:{deleted:'foreign-id'}})
  const expected=()=>page.evaluate(({kind,absent})=>window.copy(absent?(kind==='session'?'ui.store.app.495e7952925d':'ui.store.app.7666a8b4c98a'):(kind==='session'?'ui.session.deletionUnconfirmed':'ui.workspace.removalUnconfirmed')),{kind,absent})
  const wait=async()=>page.waitForFunction(expected=>document.body.textContent.includes(expected),await expected())
  await wait();const before=requests.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await wait();assert.equal(requests.length,before)
   const state=await page.evaluate(()=>window.snapshot())
   assert.equal((kind==='session'?state.sessions:state.workspaces).includes(kind==='session'?'target':'workspace'),!absent)
   assert.equal(state.draft,kind==='session'&&absent?undefined:'Original draft 原文')
  }
  assert.equal(requests.filter(request=>request.method==='DELETE').length,1)
  assert.equal(requests.filter(request=>request.method==='GET').length,1)
  assert.deepEqual(errors,[]);await page.close();console.log(kind+'/'+(absent?'absent-confirmed':'present-unconfirmed')+': actual sidebar preserves ownership and localized deletion feedback')
 }
},{entry:'/__sidebar-deletion-receipt-entry.js',vite:{plugins:[{
 name:'sidebar-deletion-receipt-fixture',resolveId(id){if(id==='/__sidebar-deletion-receipt-entry.js')return id},load(id){if(id==='/__sidebar-deletion-receipt-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__sidebar-deletion-receipt',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__sidebar-deletion-receipt','<html><body><div id="root"></div><script type="module" src="/__sidebar-deletion-receipt-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
