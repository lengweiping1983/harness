import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ProjectSidebar} from '/src/components/sidebar/ProjectSidebar.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const kind=new URLSearchParams(location.search).get('kind')
const raw={json:'{broken',shape:'{}',items:'[42]',empty:'',valid:'[]',group:'[]'}[kind]
localStorage.setItem('harness.sidebar.collapsed',raw)
localStorage.setItem('harness.sidebar.group-by',kind==='group'?'invalid':'workspace')
window.original={collapsed:localStorage.getItem('harness.sidebar.collapsed'),group:localStorage.getItem('harness.sidebar.group-by')}
useAppStore.setState({sessions:Array.from({length:6},(_,index)=>({id:'fixture-'+index,title:'Original '+index,cwd:'/fixture',created_at:1})),workspaces:[{id:'fixture',path:'/fixture',name:'Original project'}],sessionsLoading:false,workspacesLoading:false})
createRoot(document.getElementById('root')).render(React.createElement(ProjectSidebar))
`
await browserScenario(async({server,browser})=>{
 for(const kind of ['json','shape','items','empty','valid','group']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0]+'__sidebar-preferences?kind='+kind)
  await page.getByText('Original project',{exact:true}).waitFor()
  for(const locale of ['en','zh-CN','en'])for(const [theme,width] of [['light',390],['dark',1440]]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.classList.toggle('dark',theme==='dark')},{locale,theme})
   await page.getByRole('button',{name:locale==='en'?'View archived conversations':'查看已归档对话',exact:true}).click()
   await page.getByRole('button',{name:locale==='en'?'View unarchived conversations':'查看未归档对话',exact:true}).click()
   const label=await page.evaluate(()=>window.copy('ui.lib.sidebarGroups.514664132c03'))
   await page.getByRole('button',{name:label,exact:true}).click()
   const collapse=page.locator('button[title="/fixture"]')
   await collapse.click()
   await page.waitForFunction(()=>document.querySelector('button[title="/fixture"]').getAttribute('aria-expanded')==='false')
   const stored=await page.evaluate(()=>({raw:localStorage.getItem('harness.sidebar.collapsed'),original:window.original.collapsed,mode:localStorage.getItem('harness.sidebar.group-by')}))
   if(kind==='valid'||kind==='group')assert.deepEqual(JSON.parse(stored.raw),['/fixture'])
   else assert.equal(stored.raw,stored.original,'mount and edits must preserve malformed original preference')
   if(kind==='group')assert.equal(stored.mode,'invalid','malformed grouping mode must remain unchanged')
   else assert.equal(stored.mode,'tree','valid grouping mode saves independently')
   await collapse.click()
   await page.waitForFunction(()=>document.querySelector('button[title="/fixture"]').getAttribute('aria-expanded')==='true')
   if(kind!=='valid')await page.getByRole('alert').filter({hasText:await page.evaluate(()=>window.copy('ui.sidebar.preferencesUnconfirmed'))}).waitFor()
   else assert.equal(await page.getByRole('alert').count(),0)
  }
  assert.deepEqual(errors,[]);await page.close()
 }
},{entry:'/__sidebar-preferences-entry.js',vite:{plugins:[{name:'sidebar-preferences-fixture',resolveId(id){if(id==='/__sidebar-preferences-entry.js')return id},load(id){if(id==='/__sidebar-preferences-entry.js')return entry},configureServer(server){server.middlewares.use('/__sidebar-preferences',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__sidebar-preferences','<html><body><div id="root"></div><script type="module" src="/__sidebar-preferences-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
