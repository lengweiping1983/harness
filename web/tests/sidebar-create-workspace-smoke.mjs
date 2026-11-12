import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ProjectSidebar} from '/src/components/sidebar/ProjectSidebar.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.created=[]
useAppStore.setState({sessions:[{id:'orphan',title:'Original conversation',created_at:1}],workspaces:[{id:'workspace-id',path:'/projects/original',name:'Original project'}],activeSessionId:null,activeWorkspaceId:null,sessionsLoading:false,workspacesLoading:false,startNewSession:id=>window.created.push(id)})
createRoot(document.getElementById('root')).render(React.createElement(ProjectSidebar))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__sidebar-create-workspace')
 for(const locale of ['en','zh-CN','en'])for(const [dark,width] of [[false,390],[true,1440]]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark);window.created=[]},{locale,dark})
  const text=key=>page.evaluate(key=>window.copy(key),key)
  for(const mode of ['85c489da893d','514664132c03']){
   await page.getByRole('button',{name:await text('ui.lib.sidebarGroups.'+mode),exact:true}).click()
   for(const project of [true,false]){
    const group=page.locator('section').filter({hasText:project?'Original project':'Original conversation'}).first()
    const menu=group.getByRole('button',{name:await text('ui.components.sidebar.ProjectSidebar.ef430e30fb08'),exact:true})
    await menu.focus();await menu.press('ArrowDown')
    await page.getByRole('menuitem',{name:await text('ui.components.sidebar.ProjectSidebar.b4e31cb234ba'),exact:true}).click()
    assert.equal(await page.evaluate(()=>window.created.at(-1)),project?'workspace-id':null)
   }
  }
  assert.deepEqual(await page.evaluate(()=>window.created),['workspace-id',null,'workspace-id',null])
 }
 assert.deepEqual(errors,[]);await page.close()
 console.log('Sidebar new conversation: project IDs and ungrouped null preserved in workspace/tree modes across languages and layouts')
},{entry:'/__sidebar-create-workspace-entry.js',vite:{plugins:[{name:'sidebar-create-fixture',resolveId(id){if(id==='/__sidebar-create-workspace-entry.js')return id},load(id){if(id==='/__sidebar-create-workspace-entry.js')return entry},configureServer(server){server.middlewares.use('/__sidebar-create-workspace',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__sidebar-create-workspace','<html><body><div id="root"></div><script type="module" src="/__sidebar-create-workspace-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
