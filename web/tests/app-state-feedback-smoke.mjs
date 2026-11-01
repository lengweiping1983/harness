import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import App from '/src/App.tsx'
import {SettingsDialog} from '/src/components/overlays/SettingsDialog.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {api} from '/src/lib/api.ts'
import {ResourceError} from '/src/lib/resourceError.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[]
const view=new URLSearchParams(location.search).get('view')
const failure={ok:false,status:200,code:'response/invalid-json',message:'snapshot',copy:{key:'ui.http.invalidResponse'}}
for(const method of ['status','tools','skills','workspace','listSessions','listWorkspaces','models','settings'])api[method]=async()=>{window.calls.push(method);return failure}
useAppStore.setState({booted:true,booting:false,bootError:null,activeSessionId:null,sessions:[],workspaces:[{id:'workspace',path:'/fixture',name:'User 原文',color:'blue'}],toasts:[],settingsOpen:view==='language'})
if(view==='boot'){
 api.status=async()=>{window.calls.push('status');throw new ResourceError({key:'ui.http.invalidResponse'})}
 await useAppStore.getState().boot()
}else if(view==='language'){
 api.updateSettings=async()=>{window.calls.push('language-write');return {...failure,status:500,code:'settings/persist-failed'}}
 await useAppStore.getState().setLanguage('zh-CN')
}else if(view==='remove'){
 api.unregisterWorkspace=async()=>{window.calls.push('remove-write');return {ok:true,data:{deleted:'workspace'}}}
 await useAppStore.getState().removeWorkspace('workspace')
}else await useAppStore.getState().refreshSessions()
createRoot(document.getElementById('root')).render(React.createElement(view==='boot'?App:view==='language'?SettingsDialog:Toaster))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['boot','language','remove','list']){
  const page=await browser.newPage();page.setDefaultTimeout(20000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>route.fulfill({status:503,json:{error:'External 原文'}}))
  await page.goto(server.resolvedUrls.local[0]+'__app-state-feedback?view='+view)
  const expected=()=>page.evaluate(view=>view==='language'?window.copy('ui.settings.persistFailed'):view==='remove'?window.copy('ui.store.app.d01bf10fbeee',{p0:'User 原文'}):window.copy('ui.http.invalidResponse'),view)
  const wait=async()=>page.waitForFunction(expected=>document.body.textContent.includes(expected),await expected())
  await wait();const calls=await page.evaluate(()=>window.calls.length)
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await wait();assert.equal(await page.evaluate(()=>window.calls.length),calls,'locale must not repeat reads or writes')
   if(view==='remove')assert.ok((await page.locator('body').textContent()).includes('User 原文'))
  }
  assert.deepEqual(errors,[]);await page.close();console.log(view+': stored application feedback follows locale without repeating operations')
 }
},{entry:'/__app-state-feedback-entry.js',vite:{plugins:[{
 name:'app-state-feedback-fixture',resolveId(id){if(id==='/__app-state-feedback-entry.js')return id},load(id){if(id==='/__app-state-feedback-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__app-state-feedback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__app-state-feedback','<html><body><div id="root"></div><script type="module" src="/__app-state-feedback-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
