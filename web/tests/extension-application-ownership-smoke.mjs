import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PluginsPage} from '/src/components/overlays/PluginsPage.tsx'
import {EscPanel} from '/src/components/overlays/EscPanel.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
const mode=new URLSearchParams(location.search).get('mode')
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[];window.toasts=[]
useAppStore.setState({activeSessionId:'owner',toast:value=>window.toasts.push(value)})
window.switch=()=>useAppStore.setState({activeSessionId:'other'})
const agent={name:'fixture-agent',display_name:'Original Agent 原文',description:'Original description',agent_type:'agent',enabled:true,edit_scope:'user',system_prompt:'Original prompt 原文'}
let reads=0,linked=false
const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}})
window.fetch=async(path,options={})=>{
 const method=options.method||'GET';window.calls.push({path,method,body:options.body})
 if(path.endsWith('/plugin-marketplaces'))return response({marketplaces:[]})
 if(path==='/api/v1/plugin-packages'){
  reads++
  const candidates=mode==='candidates'&&reads>1?['fallback']:['old','new']
  const app={name:'fixture-app',id:mode==='identity'&&reads>1?'changed-app':'original-app',required:true,candidates,connectors:linked?[candidates[0]]:[],connected:linked}
  return response({packages:[{name:'fixture',display_name:'Original plugin',version:'1',description:'Original description',enabled:false,skills:[],connectors:[],apps:[app],hooks:[],hooks_trusted:false,hooks_fingerprint:'a'.repeat(64)}]})
 }
 if(path.includes('/apps/fixture-app/link')){linked=true;return response({ok:true})}
 if(path.includes('/agents/fixture-agent/edit'))return new Promise(resolve=>{window.finishEdit=()=>resolve(response({agent,revision:'a'.repeat(64)}))})
 if(path.startsWith('/api/v1/plugins/agent-mount'))return response({mount:{available:false,agent:null,revision:0}})
 if(path.startsWith('/api/v1/plugins?'))return response({skills:[],agents:[agent],connectors:[],enabled:{skills:true,agents:true,connectors:true}})
 return new Response(JSON.stringify({message:'Fixture unavailable'}),{status:503,headers:{'Content-Type':'application/json'}})
}
createRoot(document.getElementById('root')).render(mode==='switch'||mode==='locale'?React.createElement(EscPanel,{catalog:'agents'}):React.createElement(PluginsPage))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['candidates','identity','switch','locale']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0]+'__extension-application?mode='+mode)
  const label=key=>page.evaluate(key=>window.copy(key),key)
  if(mode==='candidates'||mode==='identity'){
   const select=page.getByRole('combobox',{name:await page.evaluate(()=>window.copy('ui.extensions.appConnector',{name:'fixture-app'}))})
   await select.selectOption('new')
   await page.getByRole('button',{name:await label('ui.extensions.refreshPlugins'),exact:true}).click()
   await page.waitForFunction(expected=>document.querySelector('select')?.value===expected,mode==='candidates'?'fallback':'old')
   const before=await page.evaluate(()=>window.calls.length)
   for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
    await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
    assert.equal(await page.locator('select').inputValue(),mode==='candidates'?'fallback':'old')
    assert.equal(await page.evaluate(()=>window.calls.length),before)
   }
   await page.getByRole('button',{name:await label('ui.extensions.linkApp'),exact:true}).click()
   await page.waitForFunction(()=>window.calls.some(call=>call.method==='POST'))
   const writes=await page.evaluate(()=>window.calls.filter(call=>call.method==='POST'))
   assert.equal(writes.length,1);assert.equal(JSON.parse(writes[0].body).connector,mode==='candidates'?'fallback':'old')
  }else{
   await page.locator('article').filter({hasText:'Original Agent 原文'}).waitFor()
   await page.locator('article').filter({hasText:'Original Agent 原文'}).locator('details summary').click()
   await page.getByRole('button',{name:await label('ui.agent.author.edit'),exact:true}).click()
   await page.waitForFunction(()=>typeof window.finishEdit==='function')
   if(mode==='switch')await page.evaluate(()=>window.switch())
   else await page.evaluate(()=>window.locale('zh-CN'))
   await page.evaluate(()=>window.finishEdit())
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
   assert.equal(await page.getByRole('dialog').count(),0,'old edit result must not open the current scope editor')
   assert.equal(await page.evaluate(()=>window.toasts.length),0)
   assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.path.includes('/agents/fixture-agent/edit')).length),1)
  }
  assert.deepEqual(errors,[]);await page.close()
 }
},{entry:'/__extension-application-entry.js',vite:{plugins:[{name:'extension-application-fixture',resolveId(id){if(id==='/__extension-application-entry.js')return id},load(id){if(id==='/__extension-application-entry.js')return entry},configureServer(server){server.middlewares.use('/__extension-application',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__extension-application','<html><body><div id="root"></div><script type="module" src="/__extension-application-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
