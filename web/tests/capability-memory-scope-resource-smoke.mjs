import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const source=readFileSync(new URL('../src/components/overlays/CapabilitiesPanel.tsx',import.meta.url),'utf8')
const limitKeys=[...source.split('const limits =')[1].split('type Limit')[0].matchAll(/(\w+): '([^']+)'/g)].map(([,field,key])=>({field,key}))
assert.equal(limitKeys.length,3)
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {CapabilitiesPanel} from '/src/components/overlays/CapabilitiesPanel.tsx'
import {MemoryPanel} from '/src/components/overlays/MemoryPanel.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',cwd:'/fixture'}]})
createRoot(document.getElementById('root')).render(React.createElement(new URLSearchParams(location.search).get('view')==='memory'?MemoryPanel:CapabilitiesPanel))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['capabilities','memory']){
  const page=await browser.newPage(),errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message))
  const config={context_auto_manage:true,context_working_budget:384000,summary_max_tokens:8192,output_reserve:16000,context_window:128000,tool_result_max_chars:12000,tool_schema_mode:'auto',permission_policy:'request_approval',...Object.fromEntries(limitKeys.map(({field},index)=>[field,index+101]))}
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),url=new URL(request.url());assert.equal(request.method(),'GET');requests.push(url.pathname+url.search)
   if(url.pathname==='/api/v1/settings')return route.fulfill({json:{config,effective:{model:'Original model 原文',context_window:12345,max_tokens:2345,sources:['manual']}}})
   assert.equal(url.pathname,'/api/v1/sessions/fixture/memory');return route.fulfill({json:{memories:[],path:'/Original/'+url.searchParams.get('scope')}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__capability-memory-scope?view='+view)
  let expectedScope='project'
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   if(view==='capabilities'){
    assert.equal(await page.locator('dl').count(),0)
    for(const {field,key} of limitKeys){assert.ok(resources[locale][key]);assert.equal(await page.getByRole('spinbutton',{name:resources[locale][key],exact:true}).inputValue(),String(config[field]))}
    assert.equal(await page.getByText('Original model 原文',{exact:true}).count(),0)
   }else{
    const select=page.getByRole('combobox',{name:resources[locale]['ui.memory.scope'],exact:true});await select.waitFor();assert.equal(await select.inputValue(),expectedScope)
    for(const scope of ['project','user','session']){
     await select.selectOption(scope);expectedScope=scope;const key='ui.memory.scope.'+scope+'.hint';assert.ok(resources[locale][key]);await page.getByText(resources[locale][key],{exact:true}).waitFor()
     await page.getByText('/Original/'+scope,{exact:true}).waitFor();assert.equal(await select.inputValue(),scope)
    }
   }
  }
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Capabilities omit the read-only summary and retain editable limit labels; three memory scope hints preserve current numeric values and paths across languages/themes/widths without writes')
},{entry:'/__capability-memory-scope-entry.js',vite:{plugins:[{name:'capability-memory-scope',resolveId(id){if(id==='/__capability-memory-scope-entry.js')return id},load(id){if(id==='/__capability-memory-scope-entry.js')return entry},configureServer(server){server.middlewares.use('/__capability-memory-scope',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__capability-memory-scope','<html><body><div id="root"></div><script type="module" src="/__capability-memory-scope-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
