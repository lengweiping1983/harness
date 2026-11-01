import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {INSPECTOR_PANELS} from '/src/lib/inspectorPanels.ts'
window.panels=INSPECTOR_PANELS
import {Inspector} from '/src/components/inspector/Inspector.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useChatStore} from '/src/store/chat.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const view=new URLSearchParams(location.search).get('view')
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',cwd:'/fixture'}],workspace:{root:view==='files-register-error'?'/other':'/fixture'},inspectorTab:view==='prompts'?'prompts':'files'})
const chat=useChatStore.getState().get('fixture')
useChatStore.setState({sessions:{fixture:{...chat,prompts:[{role:'system',text:'A😀中'},{role:'constructor',text:'Raw 原文😀'}]}}})
createRoot(document.getElementById('root')).render(React.createElement(Inspector))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['files-error','files-page','files-register-error','files-summary','prompts']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),url=new URL(request.url());requests.push({method:request.method(),path:url.pathname})
   if(url.pathname==='/api/v1/workspaces'&&view==='files-register-error')return route.fulfill({status:200,body:'not json',contentType:'application/json'})
   if(url.pathname==='/api/v1/workspace'){
    if(view==='files-error'||url.searchParams.has('offset'))return route.fulfill({status:200,body:'not json',contentType:'application/json'})
    const entries=[{name:'src',path:'src',type:'directory'},...(view==='files-summary'?[{name:'Original😀.txt',path:'Original😀.txt',type:'file',size:1}]:[])]
    return route.fulfill({json:{root:'/fixture',path:'',parents:[],exists:true,is_dir:true,entries,next_offset:view==='files-page'?1:null,query:''}})
   }
   return route.fulfill({status:503,json:{code:'fixture/offline',error:'Original external diagnostic'}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__inspector-summary?view='+view)
  if(view==='files-page'){
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.showMore')),exact:true}).click()
  }
  if(view==='prompts')await page.locator('summary').filter({hasText:await page.evaluate(()=>window.copy('ui.components.inspector.Inspector.926c503599d7'))}).click()
  const check=async locale=>{
   if(view==='files-error'||view==='files-page'||view==='files-register-error')await page.getByText(await page.evaluate(()=>window.copy('ui.http.invalidResponse')),{exact:true}).waitFor()
   if(view==='files-summary')await page.getByText(locale==='zh-CN'?'目录：1 · 文件：1':'Folders: 1 · Files: 1',{exact:true}).waitFor()
   if(view==='prompts'){
    const summary=resources[locale]['ui.prompts.summary'].replace('{count}','2').replace('{characters}','10')
    await page.getByText(summary,{exact:true}).waitFor()
    for(const count of [3,7])await page.getByText((locale==='zh-CN'?'字符数：':'Characters: ')+count,{exact:true}).waitFor()
    await page.getByText('constructor',{exact:true}).waitFor()
    for(const raw of ['A😀中','Raw 原文😀'])await page.getByText(raw,{exact:true}).waitFor()
   }
  }
  await check('en');const reads=requests.filter(request=>request.path==='/api/v1/workspace').length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   const select=page.getByRole('combobox',{name:resources[locale]['ui.workspace.switchSection'],exact:true})
   for(const panel of await page.evaluate(()=>window.panels)){
    assert.ok(resources[locale][panel.labelKey]);assert.equal(await select.locator('option[value='+panel.value+']').textContent(),resources[locale][panel.labelKey])
   }
   assert.equal(await select.inputValue(),view==='prompts'?'prompts':'files')
   await page.getByRole('tabpanel',{name:resources[locale][(await page.evaluate(()=>window.panels)).find(panel=>panel.value===(view==='prompts'?'prompts':'files')).labelKey],exact:true}).waitFor()
   await check(locale);assert.equal(requests.filter(request=>request.path==='/api/v1/workspace').length,reads)
  }
  assert.equal(reads,view==='prompts'||view==='files-register-error'?0:view==='files-page'?2:1)
  assert.equal(requests.filter(request=>request.method!=='GET').length,view==='files-register-error'?1:0);assert.deepEqual(errors,[])
  await page.close();console.log(view+': inspector feedback and full summaries follow language without reloading files')
 }
},{entry:'/__inspector-summary-entry.js',vite:{plugins:[{
 name:'inspector-summary-fixture',resolveId(id){if(id==='/__inspector-summary-entry.js')return id},load(id){if(id==='/__inspector-summary-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__inspector-summary',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__inspector-summary','<html><body><div id="root" style="height:100vh"></div><script type="module" src="/__inspector-summary-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
