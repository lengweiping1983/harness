import assert from 'node:assert/strict'
import {WebsitePermissions} from '../../desktop/permissions.mjs'
import {TabHealth} from '../../desktop/tab-health.mjs'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const manager=new WebsitePermissions(),origin='https://fixture.invalid'
for(const permission of ['notifications','geolocation','geolocation-approximate','clipboard-read','clipboard-sanitized-write','media'])manager.request('tab',origin,permission,()=>{},{requestingUrl:origin,mediaTypes:['audio','video']})
const permissionState=manager.snapshot('tab',origin)
for(const request of permissionState.requests)manager.finish(request.id,true)
permissionState.grants=manager.snapshot('tab',origin).grants
manager.clear('tab')
const health=new TabHealth(origin),issues=[]
health.failed(-2,true);issues.push(health.issue);health.gone('crashed');issues.push(health.issue);health.unresponsive();issues.push(health.issue);health.gone('clean-exit');issues.push(health.issue)
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {BrowserPanel} from '/src/components/inspector/BrowserPanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[];window.listeners=[]
const subscribe=()=>()=>{}
window.harnessDesktop={onFocusFind:subscribe,onFocusAddress:subscribe,onBrowserState:listener=>{window.pushState=listener;return()=>{}},onBrowserError:listener=>{window.external=listener;window.listeners.push(listener);return()=>{}},browser:async(action,args)=>{window.calls.push({action,args});return {session_id:'owner',active_tab:null,tabs:[]}}}
const root=createRoot(document.getElementById('root'));window.show=id=>root.render(React.createElement(BrowserPanel,{sessionId:id}));window.show('owner')
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.goto(server.resolvedUrls.local[0]+'__native-browser-feedback')
 const address=page.getByRole('textbox',{name:await page.evaluate(()=>window.copy('browser.desktop.address')),exact:true})
 await address.fill('javascript:alert(1)');await address.press('Enter')
 for(const external of [false,true]){
  if(external)await page.evaluate(()=>window.external({session_id:'owner',message:'Native original 原文'}))
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   const expected=external?'Native original 原文':await page.evaluate(()=>window.copy('browser.desktop.invalidAddress'))
   await page.getByRole('alert').getByText(expected,{exact:true}).waitFor()
   assert.equal(await page.getByRole('textbox',{name:await page.evaluate(()=>window.copy('browser.desktop.address')),exact:true}).inputValue(),'javascript:alert(1)')
  }
 }
 const calls=await page.evaluate(()=>window.calls)
 assert.equal(calls.filter(c=>c.action==='navigate').length,0)
 assert.equal(calls.filter(c=>c.action==='bind').length,1)
 assert.ok(calls.every(c=>c.args.session_id==='owner'))
 await page.evaluate(()=>window.show('next-owner'))
 await page.waitForFunction(()=>window.calls.some(call=>call.action==='bind'&&call.args.session_id==='next-owner'))
 await page.evaluate(()=>{
  window.listeners[0]({session_id:'owner',message:'Late closed instance 原文'})
  window.external({session_id:'owner',message:'Wrong conversation 原文'})
  window.external('Old unscoped string 原文')
  window.external({message:'Missing identity 原文'})
 })
 await page.evaluate(()=>new Promise(requestAnimationFrame))
 assert.equal(await page.getByRole('alert').count(),0)
 await page.evaluate(()=>window.external({session_id:'next-owner',message:'Current conversation 原文 {name}'}))
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  await page.getByRole('alert').getByText('Current conversation 原文 {name}',{exact:true}).waitFor()
 }
 await page.evaluate(()=>{
  window.external({session_id:'owner',message:'Late foreign 原文'})
  window.external({session_id:'next-owner',message:42})
 })
 await page.evaluate(()=>new Promise(requestAnimationFrame))
 await page.getByRole('alert').getByText('Current conversation 原文 {name}',{exact:true}).waitFor()
 await page.evaluate(()=>window.external({session_id:'next-owner',message:'browser/tab-limit',code:'browser/tab-limit',copy:{key:'browser.desktop.tabLimit',params:{count:12}}}))
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  await page.getByRole('alert').getByText(locale==='en'?'A conversation can have at most 12 browser tabs.':'每个对话最多可打开 12 个浏览器标签。',{exact:true}).waitFor()
 }
 assert.equal((await page.evaluate(()=>window.calls)).filter(call=>call.action==='navigate').length,0)
 const callsBefore=await page.evaluate(()=>window.calls.filter(call=>call.action!=='layout').length)
 for(const issue of issues){
  await page.evaluate(({issue,permissions})=>window.pushState({session_id:'next-owner',active_tab:'tab',tabs:[{id:'tab',url:'https://fixture.invalid',title:'Original title 原文',loading:false,issue,can_go_back:false,can_go_forward:false}],permissions}),{issue,permissions:permissionState})
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   const failureKey='browser.desktop.failure.'+issue;assert.ok(resources[locale][failureKey]);await page.getByRole('heading',{name:resources[locale][failureKey],exact:true}).waitFor()
   for(const request of permissionState.requests){
    const names=request.capabilities.map(name=>{const key='browser.desktop.permissions.'+name;assert.ok(resources[locale][key]);return resources[locale][key]}).join(resources[locale]['ui.common.list_separator'])
    await page.getByRole('region',{name:resources[locale]['browser.desktop.permissions.request'],exact:true}).getByText(resources[locale]['browser.desktop.permissions.wants'].replace('{permissions}',names),{exact:true}).waitFor()
   }
   const grants=permissionState.grants.map(name=>resources[locale]['browser.desktop.permissions.'+name]).join(resources[locale]['ui.common.list_separator'])
   await page.getByText(grants,{exact:true}).waitFor();assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.action!=='layout').length),callsBefore)
  }
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Native browser: local errors follow language; external text and address preserved, no navigation replay')
},{entry:'/__native-browser-feedback-entry.js',vite:{plugins:[{
 name:'native-browser-feedback-fixture',resolveId(id){if(id==='/__native-browser-feedback-entry.js')return id},load(id){if(id==='/__native-browser-feedback-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__native-browser-feedback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__native-browser-feedback','<html><body><div id="root"></div><script type="module" src="/__native-browser-feedback-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
