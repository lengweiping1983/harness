import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PluginSourceEditor} from '/src/components/overlays/PluginSourceEditor.tsx'
import {PluginMarketplaces} from '/src/components/overlays/PluginMarketplaces.tsx'
import {PluginsPage} from '/src/components/overlays/PluginsPage.tsx'
import {PluginHooksSection} from '/src/components/overlays/PluginHooks.tsx'
import {ProjectHooksPage} from '/src/components/overlays/ProjectHooksPage.tsx'
import {AgentSkillPicker} from '/src/components/overlays/AgentSkillPicker.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
const fetch=window.fetch.bind(window)
window.fetch=(...args)=>fetch(...args).catch(error=>{window.networkDetail=error.message;throw error})
useAppStore.setState({activeSessionId:'fixture',settingsWorkspaceRoot:'/fixture'})
const noop=()=>{}
const views={source:[PluginSourceEditor,{root:'/fixture',onReview:noop,onChanged:noop}],market:[PluginMarketplaces,{onPreview:noop}],'market-unknown':[PluginMarketplaces,{onPreview:noop}],plugins:[PluginsPage,{}],'plugin-hooks':[PluginHooksSection,{}],hooks:[ProjectHooksPage,{}],skills:[AgentSkillPicker,{sessionId:'fixture',value:null,onChange:()=>{throw Error('Unexpected selection change')}}]}
const [Component,props]=views[new URLSearchParams(location.search).get('view')]
createRoot(document.getElementById('root')).render(React.createElement(Component,props))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['source','market','market-unknown','plugins','plugin-hooks','hooks','skills']){
  const page=await browser.newPage();page.setDefaultTimeout(15000);await page.clock.install()
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request();requests.push({method:request.method(),url:request.url()})
   if(view==='market-unknown')return request.method()==='POST'?route.abort('failed'):route.fulfill({json:{marketplaces:[]}})
   return route.fulfill({status:200,body:'not json',contentType:'application/json'})
  })
  await page.goto(server.resolvedUrls.local[0]+'__extension-error?view='+view)
  if(view==='skills')await page.locator('summary').click()
  if(view==='market-unknown'){
   await page.getByRole('textbox',{name:await page.evaluate(()=>window.copy('ui.extensions.marketRoot')),exact:true}).fill('/Original market')
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.extensions.addMarket')),exact:true}).click()
   await page.waitForFunction(()=>typeof window.networkDetail==='string')
  }
  const expected=()=>page.evaluate(view=>view==='market-unknown'?window.copy('ui.extensions.unknownResult',{reason:window.copy('ui.lib.api.4a5f658ed9ae',{detail:window.networkDetail})}):window.copy('ui.http.invalidResponse'),view)
  const wait=async()=>page.waitForFunction(text=>Array.from(document.querySelectorAll('[role=alert]')).some(element=>element.textContent.startsWith(text)),await expected())
  await wait();await page.clock.pauseAt(await page.evaluate(()=>Date.now()+1000));const before=requests.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await wait()
   if(view!=='skills')assert.equal(requests.length,before,view+': language must not repeat operations')
  }
  if(view==='skills')assert.equal(requests.length,3,'skill descriptions reload once per actual language change')
  assert.equal(requests.filter(request=>request.method!=='GET').length,view==='market-unknown'?1:0)
  assert.deepEqual(errors,[],view);await page.close();console.log(view+': feedback follows language, writes and selection remain isolated')
 }
},{entry:'/__extension-error-entry.js',vite:{plugins:[{
 name:'extension-error-language-fixture',resolveId(id){if(id==='/__extension-error-entry.js')return id},load(id){if(id==='/__extension-error-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__extension-error',async(_req,res,next)=>{
  try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__extension-error','<html><body><div id="root"></div><script type="module" src="/__extension-error-entry.js"></script></body></html>'))}catch(error){next(error)}
 })},
}]}})
