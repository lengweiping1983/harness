import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PluginsPage} from '/src/components/overlays/PluginsPage.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[]
const view=new URLSearchParams(location.search).get('view');let reads=0,removed=false
const item=name=>({name,display_name:name,version:'1',description:'Original description 原文',enabled:false,skills:[],connectors:[],apps:[],hooks:[],hooks_trusted:false,hooks_fingerprint:'a'.repeat(64)})
const catalog=name=>({packages:removed?[]:[item(name)]})
const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}})
window.fetch=async(path,options={})=>{
 const method=options.method||'GET';window.calls.push({path,method})
 if(path==='/api/v1/plugin-packages'&&method==='GET'){
  if(++reads===1&&view!=='remove'){
   window.oldSignal=options.signal
   return new Promise((resolve,reject)=>{window.releaseOld=()=>{(view==='late-failure'||view==='strict-failure')?reject(Error('Obsolete catalog failure 原文')):resolve(response(catalog('Obsolete plugin')))}})
  }
  return response(catalog('Current plugin'))
 }
 if(path==='/api/v1/plugin-packages/Current%20plugin'&&method==='DELETE'){removed=true;return response({ok:true})}
 if(path==='/api/v1/plugin-marketplaces')return response({marketplaces:[]})
 throw Error('Unexpected plugin request '+method+' '+path)
}
createRoot(document.getElementById('root')).render(view==='strict-failure'?React.createElement(React.StrictMode,null,React.createElement(PluginsPage)):React.createElement(PluginsPage))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['late-failure','late-success','strict-failure','remove']){
  const page=await browser.newPage(),errors=[];page.setDefaultTimeout(15000);page.on('pageerror',error=>errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0]+'__plugin-page?view='+view)
  const text=key=>page.evaluate(key=>window.copy(key),key)
  if(view==='remove'){
   await page.getByText('Current plugin',{exact:true}).waitFor()
   await page.getByRole('button',{name:await text('ui.extensions.remove'),exact:true}).click()
   await page.getByRole('button',{name:await text('ui.extensions.confirmRemove'),exact:true}).click()
   await page.getByText(await text('ui.extensions.removed'),{exact:true}).waitFor()
  }else{
   await page.waitForFunction(()=>typeof window.releaseOld==='function')
   if(view!=='strict-failure')await page.getByRole('button',{name:await text('ui.extensions.refreshPlugins'),exact:true}).click()
   await page.getByText('Current plugin',{exact:true}).waitFor()
   await page.evaluate(()=>window.releaseOld())
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
   assert.equal(await page.getByRole('alert').count(),0,'Obsolete failure must not overwrite successful refresh')
   assert.equal(await page.evaluate(()=>window.oldSignal?.aborted),true)
  }
  assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.path==='/api/v1/plugin-packages'&&call.method==='GET').length),2,'Initial load plus explicit refresh or post-write reload must each read once')
  const before=await page.evaluate(()=>window.calls.length)
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   if(view==='remove')await page.getByText(await text('ui.extensions.removed'),{exact:true}).waitFor()
   else {await page.getByText('Current plugin',{exact:true}).waitFor();assert.equal(await page.getByText('Obsolete plugin',{exact:true}).count(),0);assert.equal(await page.getByRole('alert').count(),0)}
   assert.equal(await page.evaluate(()=>window.calls.length),before)
  }
  assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.method!=='GET').length),view==='remove'?1:0)
  assert.deepEqual(errors,[]);await page.close();console.log(view+': plugin page keeps current catalog and language without write replay')
 }
},{entry:'/__plugin-page-entry.js',vite:{plugins:[{
 name:'plugin-page-fixture',resolveId(id){if(id==='/__plugin-page-entry.js')return id},load(id){if(id==='/__plugin-page-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__plugin-page',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__plugin-page','<html><body><div id="root"></div><script type="module" src="/__plugin-page-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
