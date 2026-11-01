import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PluginMarketplaces} from '/src/components/overlays/PluginMarketplaces.tsx'
import {PluginHooksSection} from '/src/components/overlays/PluginHooks.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;window.calls=[];setLocale('en')
const view=new URLSearchParams(location.search).get('view');let reads=0
const market=name=>({marketplaces:[{name,display_name:name,path:'/fixture',fingerprint:'a'.repeat(64),error:'',registered:true,plugins:[]}]})
const hooks=trusted=>({packages:[{name:'fixture',version:'1',description:'Original description 原文',display_name:'Fixture plugin',skills:[],connectors:[],hooks_fingerprint:'a'.repeat(64),hooks_trusted:trusted,hooks:[{id:'hook',event:'PreToolUse',type:'command',command:'echo Original 原文',label:'Original hook',timeout:1}]}]})
const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}})
window.fetch=async(path,options={})=>{
 const method=options.method||'GET';window.calls.push({path,method})
 if(method==='GET'&&++reads===1){window.oldSignal=options.signal;return new Promise(resolve=>{window.releaseOld=()=>resolve(response(view==='hooks'?hooks(false):market('Obsolete market')))})}
 if(view==='hooks')return response(hooks(method==='POST'))
 return response(market('Current market'))
}
const component=view==='hooks'?React.createElement(PluginHooksSection):React.createElement(PluginMarketplaces,{onPreview:()=>{}})
createRoot(document.getElementById('root')).render(view==='hooks'?React.createElement(React.StrictMode,null,component):component)
`
await browserScenario(async({server,browser})=>{
 for(const view of ['refresh','register','hooks']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0]+'__catalog-owner?view='+view)
  await page.waitForFunction(()=>typeof window.releaseOld==='function')
  const text=key=>page.evaluate(key=>window.copy(key),key)
  if(view==='hooks'){
   await page.locator('summary').click()
   await page.getByRole('button',{name:await text('ui.extensions.hooksTrust'),exact:true}).click()
   await page.getByRole('button',{name:await text('ui.extensions.hooksRevoke'),exact:true}).waitFor()
  }else if(view==='register'){
   await page.getByRole('textbox',{name:await text('ui.extensions.marketRoot'),exact:true}).fill('/fixture')
   await page.getByRole('button',{name:await text('ui.extensions.addMarket'),exact:true}).click()
  }else await page.getByRole('button',{name:await text('ui.extensions.reloadMarkets'),exact:true}).click()
  if(view!=='hooks')await page.getByText('Current market',{exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>window.oldSignal.aborted),true)
  await page.evaluate(()=>window.releaseOld())
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
  const count=await page.evaluate(()=>window.calls.length)
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   if(view==='hooks')await page.getByRole('button',{name:await text('ui.extensions.hooksRevoke'),exact:true}).waitFor()
   else {await page.getByText('Current market',{exact:true}).waitFor();assert.equal(await page.getByText('Obsolete market',{exact:true}).count(),0)}
   assert.equal(await page.evaluate(()=>window.calls.length),count)
  }
  const calls=await page.evaluate(()=>window.calls);assert.equal(calls.filter(x=>x.method==='POST').length,view==='refresh'?0:1)
  assert.deepEqual(errors,[]);await page.close();console.log('Superseded initial catalog cannot overwrite current results: '+view)
 }
},{entry:'/__catalog-owner-entry.js',vite:{plugins:[{name:'catalog-owner-fixture',resolveId(id){if(id==='/__catalog-owner-entry.js')return id},load(id){if(id==='/__catalog-owner-entry.js')return entry},configureServer(server){server.middlewares.use('/__catalog-owner',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__catalog-owner','<html><body><div id="root"></div><script type="module" src="/__catalog-owner-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
