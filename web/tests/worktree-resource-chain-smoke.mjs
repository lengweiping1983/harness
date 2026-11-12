import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const reasons=['exists','not-ignored','symlink','unsafe','unsupported','missing','constructor','Original unknown 原文']
const unavailable=['symlink','unsupported','large','binary']
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {WorktreeEnvironment} from '/src/components/overlays/WorktreeEnvironment.tsx'
import {WorktreeReview} from '/src/components/overlays/WorktreeReview.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale
createRoot(document.getElementById('root')).render(React.createElement('main',null,
 ...['preparing','ready','failed'].map(status=>React.createElement('section',{'data-environment':status,key:status},React.createElement(WorktreeEnvironment,{id:status,sessionId:'fixture',defaultOpen:true}))),
 ...['symlink','unsupported','large','binary'].map(reason=>React.createElement('section',{'data-review':reason,key:reason},React.createElement(WorktreeReview,{id:reason,defaultOpen:true})))))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];let reads=0
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{
  assert.equal(route.request().method(),'GET');reads++;const path=new URL(route.request().url()).pathname
  if(path.endsWith('/review')){const reason=path.split('/').at(-2);return route.fulfill({json:{base_commit:'a'.repeat(40),base_kind:'creation',files:[{path:'Original 文件 '+reason,status:'M',kind:'tracked'}],...(new URL(route.request().url()).searchParams.has('path')?{selection:{path:'Original 文件 '+reason,diff:'',truncated:false,unavailable:reason}}:{})}})}
  const receipt=path.endsWith('/receipt'),status=path.split('/').at(receipt?-3:-2)
  const entries=reasons.map((reason,index)=>({path:'Original 文件 '+index,action:'skip',reason}))
  return route.fulfill({json:receipt?{receipt:{receipt_id:status,worktree_id:status,fingerprint:'a'.repeat(64),status,planned_count:0,copied_count:0,skipped_count:entries.length,reused:false,truncated:false,entries}}:{configured:true,fingerprint:'a'.repeat(64),copy_count:0,skip_count:entries.length,total_bytes:0,offset:0,next_offset:null,entries}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__worktree-resource-chain')
 for(const status of ['preparing','ready','failed'])await page.locator('[data-environment='+status+']').getByText(resources.en['ui.environment.receipt'],{exact:true}).click()
 for(const reason of unavailable)await page.locator('[data-review='+reason+']').getByRole('button',{name:'M Original 文件 '+reason,exact:true}).click()
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const status of ['preparing','ready','failed']){
   const section=page.locator('[data-environment='+status+']');const key='ui.environment.status.'+status;assert.ok(resources[locale][key]);await section.getByText(resources[locale][key],{exact:true}).waitFor()
   for(const reason of reasons){const key='ui.environment.reason.'+(reasons.slice(0,6).includes(reason)?reason:'unsupported');assert.ok(resources[locale][key]);assert.ok(await section.getByText(resources[locale][key],{exact:true}).count()>=2)}
   for(let index=0;index<reasons.length;index++)assert.equal(await section.getByText('Original 文件 '+index,{exact:true}).count(),2)
  }
  for(const reason of unavailable){const key='ui.worktree.review.'+reason;assert.ok(resources[locale][key]);await page.locator('[data-review='+reason+']').getByText(resources[locale][key],{exact:true}).waitFor()}
  assert.equal(reads,14)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Worktree environment reasons/receipt states and review refusal reasons translate without rereads or writes; original paths preserved')
},{entry:'/__worktree-resource-chain-entry.js',vite:{plugins:[{name:'worktree-resource-chain',resolveId(id){if(id==='/__worktree-resource-chain-entry.js')return id},load(id){if(id==='/__worktree-resource-chain-entry.js')return entry},configureServer(server){server.middlewares.use('/__worktree-resource-chain',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__worktree-resource-chain','<html><body><div id="root"></div><script type="module" src="/__worktree-resource-chain-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
