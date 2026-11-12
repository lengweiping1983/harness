import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PermissionMenu} from '/src/components/chat/PermissionMenu.tsx'
import {ModelConnections} from '/src/components/overlays/ModelConnections.tsx'
import {QuickOpen} from '/src/components/overlays/QuickOpen.tsx'
import {useAppStore} from '/src/store/app.ts'
import {usePermissionStore} from '/src/store/permissions.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;window.permissions=usePermissionStore;setLocale('en')
const view=new URLSearchParams(location.search).get('view')
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',cwd:'/root'}],quickOpen:view==='files'})
const content=view==='files'?React.createElement(QuickOpen):view==='connection'?React.createElement(ModelConnections):React.createElement(PermissionMenu,{connection:'open'})
createRoot(document.getElementById('root')).render(content)
`
await browserScenario(async({server,browser})=>{
 for(const view of ['permission-read','permission-save','connection','files']){
  const page=await browser.newPage();page.setDefaultTimeout(15000);await page.clock.install()
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),url=new URL(request.url());requests.push({method:request.method(),path:url.pathname})
   if(url.pathname==='/api/v1/chatgpt')return route.fulfill({json:{logged_in:false,email:'',pending:false,error:'',selected:'',models:[]}})
   if(view==='permission-save'&&request.method()==='GET')return route.fulfill({json:{permissions:{session_id:'fixture',scope:'fixture',permission_policy:'request_approval',default_policy:'request_approval',inherited:false,version:'a'.repeat(64)}}})
   return route.fulfill({status:200,body:'not json',contentType:'application/json'})
  })
  await page.goto(server.resolvedUrls.local[0]+'__state-error?view='+view)
  if(view.startsWith('permission')){
   await page.waitForFunction(()=>!window.permissions.getState().loading && (window.permissions.getState().readError || window.permissions.getState().policy))
   await page.getByRole('button').first().click()
   if(view==='permission-save')await page.getByRole('menuitemradio').last().click()
  }
  const expected=()=>page.evaluate(view=>{
   const detail=window.copy('ui.http.invalidResponse')
   if(view.startsWith('permission'))return window.copy(view==='permission-read'?'ui.components.chat.PermissionMenu.62a03d787939':'ui.components.chat.PermissionMenu.375506c3c436',{detail})
   return detail
  },view)
  await page.getByText(await expected(),{exact:true}).waitFor()
  await page.clock.pauseAt(await page.evaluate(()=>Date.now()+1000))
  const before=requests.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await page.getByText(await expected(),{exact:true}).waitFor()
   assert.equal(requests.length,before,view+': language changed requests')
   if(locale==='en'&&view.startsWith('permission'))assert.ok(!(await expected()).includes('：'))
  }
  if(view==='permission-save')assert.equal(requests.filter(request=>request.method==='PATCH').length,1)
  assert.deepEqual(errors,[],view)
  await page.close();console.log(view+': saved errors update language without repeating operations')
 }
},{entry:'/__state-error-entry.js',vite:{plugins:[{
 name:'state-error-language-fixture',resolveId(id){if(id==='/__state-error-entry.js')return id},load(id){if(id==='/__state-error-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__state-error',async(_req,res,next)=>{
  try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__state-error','<html><body><div id="root"></div><script type="module" src="/__state-error-entry.js"></script></body></html>'))}catch(error){next(error)}
 })},
}]}})
