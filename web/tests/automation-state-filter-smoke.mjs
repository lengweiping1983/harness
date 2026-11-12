import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entryPath='/__automation-filter-entry.js'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AutomationsPage} from '/src/components/overlays/AutomationsPage.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.copy=t;window.language=setLocale;window.methods=[]
const task=(id,enabled=0,once=false)=>({id,name:id,prompt:'Original prompt 原文',session_id:'fixture',mode:'session',schedule:once?{kind:'once',at:100}:{kind:'interval',seconds:1800},timezone:'UTC',due:100,enabled,running:0,failures:0,last_error:null})
const run=(id,status,finished)=>({id:'run-'+id,automation_id:id,name:id,session_id:'fixture',started:1,finished,status,trigger:'manual',unread:0,result:'{}'})
window.fetch=async(_url,options={})=>{window.methods.push(options.method||'GET');return Response.json({
 automations:[task('Active 原文',1),task('Paused 原文'),task('Completed 原文',0,true),{...task('Future 原文',0,true),due:500}],
 runs:[run('Completed 原文','completed',200),run('Future 原文','completed',200)],scheduler:{running:true,owner:true,last_error:null,max_concurrent:3}})}
createRoot(document.getElementById('root')).render(React.createElement(AutomationsPage))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__automation-filter')
 const copy=key=>page.evaluate(key=>window.copy(key),key)
 for(const width of [390,1440])for(const dark of [false,true])for(const locale of ['en','zh-CN','en']){
  await page.setViewportSize({width,height:1000})
  await page.evaluate(async({locale,dark})=>{await window.language(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const filter=page.getByRole('combobox',{name:await copy('ui.components.overlays.AutomationsPage.3ed6012d1467'),exact:true})
  const search=page.getByRole('textbox',{name:await copy('ui.components.overlays.AutomationsPage.374b7c866c67'),exact:true})
  await filter.selectOption('paused')
  assert.deepEqual(await page.locator('article h4').allTextContents(),['Paused 原文','Future 原文'],'A completed run before the due time does not finish a paused one-off task')
  await filter.selectOption('completed')
  assert.deepEqual(await page.locator('article h4').allTextContents(),['Completed 原文'])
  assert.equal(await page.locator('article').getByText(await copy('ui.components.overlays.AutomationsPage.f28461bb49c8'),{exact:true}).count(),1)
  await filter.selectOption('active')
  assert.deepEqual(await page.locator('article h4').allTextContents(),['Active 原文'])
  await filter.selectOption('all');await search.fill('   PAUSED 原文   ')
  assert.deepEqual(await page.locator('article h4').allTextContents(),['Paused 原文'])
  await search.fill('No matching task')
  assert.equal(await page.locator('article').count(),0)
  await page.getByRole('button',{name:await copy('ui.automations.resetFilters'),exact:true}).click()
  assert.equal(await search.inputValue(),'');assert.equal(await filter.inputValue(),'all')
  assert.equal(await page.locator('article').count(),4)
  assert.equal(await page.locator('body').evaluate(el=>el.scrollWidth<=innerWidth),true)
  if(locale==='zh-CN'&&!dark&&width===1440&&process.env.HARNESS_TEST_AUTOMATION_FILTER_SCREENSHOT)await page.screenshot({path:process.env.HARNESS_TEST_AUTOMATION_FILTER_SCREENSHOT})
 }
 assert.ok((await page.evaluate(()=>window.methods)).every(method=>method==='GET'))
 assert.deepEqual(errors,[])
 console.log('Automation filters: active/paused/completed, future one-off manual run, trimmed search, reset, bilingual themes and widths passed')
},{entry:entryPath,vite:{plugins:[{name:'automation-filter-fixture',resolveId(id){if(id===entryPath)return id},load(id){if(id===entryPath)return entry},configureServer(server){server.middlewares.use('/__automation-filter',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__automation-filter','<html><body><div id="root"></div><script type="module" src="'+entryPath+'"></script></body></html>'))}catch(error){next(error)}})}}]}})
