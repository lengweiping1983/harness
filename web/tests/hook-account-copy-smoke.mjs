import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PluginHookReview} from '/src/components/overlays/PluginHooks.tsx'
import {ChatGPTLogin} from '/src/components/overlays/ChatGPTLogin.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
const item={hooks:[{event:'PreToolUse',type:'command',timeout:3,command:'Original command 原文'}]}
const noop=()=>{}
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,
 ...[false,true].map(trusted=>React.createElement(PluginHookReview,{key:String(trusted),item:{...item,hooks_trusted:trusted},busy:false,onTrust:noop})),
 React.createElement(ChatGPTLogin,{onStatus:noop})))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();const errors=[];let reads=0
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v1/**',route=>{reads++;assert.equal(route.request().method(),'GET');return route.fulfill({json:{logged_in:true,email:'Original@example.com',selected:'fixture',pending:false,models:[],error:''}})})
 await page.goto(server.resolvedUrls.local[0]+'__hook-account')
 await page.getByText('Signed in Original@example.com',{exact:true}).waitFor()
 const count=reads
 for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({language,theme})=>{window.locale(language);document.documentElement.classList.toggle('dark',theme==='dark')},{language,theme})
  assert.equal(await page.evaluate(()=>document.documentElement.classList.contains('dark')),theme==='dark')
  for(const trusted of [false,true]){
   const expected=language==='en'?`Hook review (1) · ${trusted?'Trusted':'Review required'}`:`审核 1 个钩子 · ${trusted?'已信任':'待审核'}`
   const summary=page.locator('summary').filter({hasText:expected});await summary.waitFor()
   if(!await summary.evaluate(node=>node.parentElement.open))await summary.click()
  }
  assert.equal(await page.getByText(language==='en'?'PreToolUse · command · 3s':'PreToolUse · command · 3 秒',{exact:true}).count(),2)
  assert.equal(await page.getByText('Original command 原文',{exact:true}).count(),2)
  await page.getByText(language==='en'?'Signed in Original@example.com':'已登录 Original@example.com',{exact:true}).waitFor()
  assert.equal(reads,count)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Hook summary/metadata and account identity follow language without rereading; original command/email preserved')
},{entry:'/__hook-account-entry.js',vite:{plugins:[{name:'hook-account-fixture',resolveId(id){if(id==='/__hook-account-entry.js')return id},load(id){if(id==='/__hook-account-entry.js')return entry},configureServer(server){server.middlewares.use('/__hook-account',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__hook-account','<html><body><div id="root"></div><script type="module" src="/__hook-account-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
