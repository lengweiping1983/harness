import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PluginsPage} from '/src/components/overlays/PluginsPage.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[];window.pending=[];window.hold=false
const item=name=>({name,display_name:name,version:'1',description:'Original description 原文',enabled:false,skills:[],connectors:[],apps:[],hooks:[],hooks_trusted:false,hooks_fingerprint:'a'.repeat(64)})
const market=name=>({marketplaces:[{name,display_name:name,path:'/fixture',fingerprint:'a'.repeat(64),error:'',registered:true,plugins:[]}]})
window.fetch=async(path,options={})=>{
 const method=options.method||'GET';window.calls.push({path,method})
 if(window.hold)return new Promise(resolve=>window.pending.push({path,method,signal:options.signal,resolve}))
 return Response.json(path.endsWith('/plugin-packages')?{packages:[item('Original plugin')]}:market('Original market'))
}
window.answer=(suffix,failed=false)=>{
 const index=window.pending.findIndex(row=>row.path.endsWith(suffix));const [row]=window.pending.splice(index,1)
 row.resolve(failed?Response.json({message:'Controlled catalog failure'},{status:503}):Response.json(suffix==='/plugin-packages'?{packages:[item('Current plugin')]}:market('Current market')))
}
const root=createRoot(document.getElementById('root'));window.close=()=>root.unmount()
root.render(React.createElement('main',{className:'min-h-screen bg-surface p-4 text-ink'},React.createElement(PluginsPage)))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.setDefaultTimeout(5000)
 page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__plugins-refresh')
 await page.getByText('Original plugin',{exact:true}).waitFor()
 await page.getByText('Original market',{exact:true}).waitFor()
 const refresh=()=>page.getByRole('button',{name:pageLocale==='en'?'Refresh plugins and market catalogs':'刷新插件与市场目录',exact:true})
 let pageLocale='en'
 const matrix=async()=>{
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   pageLocale=locale;await page.setViewportSize({width,height:1000})
   await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   await refresh().waitFor()
   assert.equal(await page.locator('button.refresh-action').count(),1,'Plugin page exposes one refresh control')
   assert.equal(await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.extensions.reloadMarkets')),exact:true}).count(),0)
   assert.equal(await page.locator('body').evaluate(node=>node.scrollWidth<=innerWidth+1),true)
   if(locale==='zh-CN'&&!dark&&width===1440&&process.env.HARNESS_TEST_PLUGINS_SCREENSHOT&&await page.getByRole('alert').count()===0)await page.screenshot({path:process.env.HARNESS_TEST_PLUGINS_SCREENSHOT})
  }
 }
 await matrix();assert.equal(await page.evaluate(()=>window.calls.length),2)
 await page.evaluate(()=>window.hold=true)
 await refresh().evaluate(button=>{button.click();button.click()})
 await page.waitForFunction(()=>window.pending.length===2)
 assert.equal(await refresh().isDisabled(),true)
 assert.equal(await page.evaluate(()=>window.calls.length),4,'Both catalogs are read once per click')
 await page.evaluate(()=>window.answer('/plugin-packages'))
 await page.getByText('Current plugin',{exact:true}).waitFor()
 assert.equal(await refresh().isDisabled(),true,'Wait for the market catalog too')
 await page.evaluate(()=>window.answer('/plugin-marketplaces'))
 await page.getByText('Current market',{exact:true}).waitFor()
 await page.waitForFunction(()=>!document.querySelector('button.refresh-action').disabled)
 await refresh().click();await page.waitForFunction(()=>window.pending.length===2)
 await page.evaluate(()=>{window.answer('/plugin-packages');window.answer('/plugin-marketplaces',true)})
 await page.getByRole('alert').waitFor()
 await page.waitForFunction(()=>!document.querySelector('button.refresh-action').disabled)
 await matrix()
 await page.getByText('Current market',{exact:true}).waitFor()
 assert.equal(await page.getByText(/result.*unconfirmed|结果.*未确认/i).count(),0,'Read failure must not be described as an unknown write')
 await refresh().click();await page.waitForFunction(()=>window.pending.length===2)
 await page.evaluate(()=>{window.answer('/plugin-packages');window.answer('/plugin-marketplaces')})
 await page.waitForFunction(()=>!document.querySelector('button.refresh-action').disabled)
 assert.equal(await page.getByRole('alert').count(),0)
 const input=page.getByRole('textbox',{name:await page.evaluate(()=>window.copy('ui.extensions.marketRoot')),exact:true})
 await input.fill('/new-market')
 const register=page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.extensions.addMarket')),exact:true})
 await register.click();await page.waitForFunction(()=>window.pending.length===1)
 assert.equal(await refresh().isDisabled(),true,'Market registration blocks the page refresh')
 const count=await page.evaluate(()=>window.calls.length)
 await refresh().evaluate(button=>button.click())
 assert.equal(await page.evaluate(()=>window.calls.length),count)
 await page.evaluate(()=>window.answer('/plugin-marketplaces'))
 await page.waitForFunction(()=>!document.querySelector('button.refresh-action').disabled)
 assert.equal(await page.evaluate(()=>window.calls.filter(row=>row.method!=='GET').length),1,'Refreshes must not register, sync or install anything')
 await refresh().click();await page.waitForFunction(()=>window.pending.length===2)
 await page.evaluate(()=>window.close())
 await page.waitForFunction(()=>window.pending.every(row=>row.signal?.aborted))
 await page.evaluate(()=>{window.answer('/plugin-packages');window.answer('/plugin-marketplaces',true)})
 assert.equal(await page.getByRole('alert').count(),0,'Closed settings must not publish a late read error')
 assert.deepEqual(errors,[]);await page.close()
 console.log('Plugin page single refresh: both catalogs once, shared busy lifetime, read-only failures/recovery, registration isolation, bilingual themes and sizes passed')
},{entry:'/__plugins-refresh-entry.js',vite:{plugins:[{
 name:'plugins-refresh-fixture',resolveId(id){if(id==='/__plugins-refresh-entry.js')return id},load(id){if(id==='/__plugins-refresh-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__plugins-refresh',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__plugins-refresh','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__plugins-refresh-entry.js"></script></body></html>'))}catch(error){next(error)}})},
}]}})
