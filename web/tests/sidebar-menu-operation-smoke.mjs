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
 for(const [operation,suffix] of [['fork','90d990618b19'],['continue','ba801e773984'],['archive','3eecd09d0bae']]){
  const page=await browser.newPage(),requests=[],errors=[]
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  page.on('pageerror',error=>errors.push(error.message))
  let pending,accepted;const started=new Promise(resolve=>{accepted=resolve})
  await page.route('**/api/v1/**',route=>{
   const request=route.request();requests.push(request.method())
   if(request.method()==='POST'){pending=route;accepted();return}
   return route.fulfill({json:{sessions:operation==='fork'?[session,{...session,id:'child',title:'Original child'}]:[session]}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__sidebar-menu-operation')
  const choose=async()=>{
   await page.getByRole('button',{name:'Conversation 原文',exact:true}).click({button:'right'})
   await page.getByRole('menuitem',{name:await page.evaluate(suffix=>window.copy('ui.components.sidebar.ProjectSidebar.'+suffix),suffix),exact:true}).click()
  }
  await choose();await started;await choose();await page.clock.runFor(32)
  assert.equal(requests.filter(method=>method==='POST').length,1,'reopening must share accepted '+operation)
  await pending.fulfill({status:503,json:{code:'fixture/unknown',message:'Original diagnostic 原文'}})
  const wait=async()=>page.waitForFunction(()=>document.body.textContent.includes(window.copy('ui.sidebar.operationUnconfirmed')))
  await wait();assert.deepEqual(requests,['POST','GET'])
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.classList.toggle('dark',theme==='dark')},{locale,theme})
   await wait();assert.deepEqual(requests,['POST','GET'])
   const state=await page.evaluate(()=>window.snapshot())
   assert.equal(state.draft,'Original draft 原文')
   assert.deepEqual(state.sessions,operation==='fork'?['target','child']:['target'])
  }
  assert.deepEqual(errors,[]);await page.close()
 }
},{entry:'/__sidebar-menu-operation-entry.js',vite:{plugins:[{
 name:'sidebar-menu-operation-fixture',resolveId(id){if(id==='/__sidebar-menu-operation-entry.js')return id},load(id){if(id==='/__sidebar-menu-operation-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__sidebar-menu-operation',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__sidebar-menu-operation','<html><body><div id="root"></div><script type="module" src="/__sidebar-menu-operation-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
