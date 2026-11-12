import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const catalogs=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AgentDefinitionCreator} from '/src/components/overlays/AgentDefinitionCreator.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.refreshes=0
const seed={name:'fixture',display_name:'Original Agent 原文',description:'Original description 原文',agent_type:'agent',system_prompt:'Original instructions 原文',metadata:{personality_prompt:'Original personality 原文'},skills:[]}
createRoot(document.getElementById('root')).render(React.createElement(AgentDefinitionCreator,{sessionId:null,seed,agents:[],refresh:async()=>{window.refreshes++}}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],writes=[];page.setDefaultTimeout(10000)
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(request.method()!=='GET')writes.push(path)
  if(request.method()==='GET'&&path==='/api/v1/personalities')return route.fulfill({json:{profiles:['builtin','user'].map(source=>({id:source,code:source,source,name:'Original '+source+' 原文',description:'Original profile 原文',prompt:'Original '+source+' prompt 原文'}))}})
  if(request.method()==='GET'&&path==='/api/v1/external-agents')return route.fulfill({json:{runners:[],platform:'posix',delegation_enabled:true}})
  return route.fulfill({status:500,json:{error:'Unexpected request'}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__agent-personality-resource')
 const dialog=page.getByRole('dialog')
 await dialog.locator('summary').getByText(catalogs.en['ui.agent.author.personalityOptional'],{exact:true}).click()
 await dialog.locator('summary').getByText(catalogs.en['ui.personality.reference'],{exact:true}).click()
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const source of ['builtin','user','all']){
   const tab=dialog.getByRole('button',{name:catalogs[locale]['ui.personality.tab.'+source],exact:true})
   await tab.click();assert.equal(await tab.getAttribute('aria-pressed'),'true')
   for(const profile of ['builtin','user']){
    const card=dialog.getByRole('button').filter({hasText:'Original '+profile+' 原文'})
    if(source==='all'||source===profile)await card.waitFor()
    else assert.equal(await card.count(),0)
   }
  }
  assert.equal(await dialog.getByRole('textbox',{name:catalogs[locale]['ui.personality.content'],exact:true}).inputValue(),'Original personality 原文')
  assert.equal(await dialog.getByRole('textbox',{name:catalogs[locale]['ui.agent.author.friendlyName'],exact:true}).inputValue(),'Original Agent 原文')
  assert.deepEqual(writes,[]);assert.equal(await page.evaluate(()=>window.refreshes),0)
 }
 await dialog.getByRole('button').filter({hasText:'Original user 原文'}).click()
 assert.equal(await dialog.getByRole('textbox',{name:catalogs.en['ui.personality.content'],exact:true}).inputValue(),'Original user prompt 原文')
 assert.deepEqual(writes,[]);assert.deepEqual(errors,[])
},{entry:'/__agent-personality-resource-entry.js',vite:{plugins:[{name:'agent-personality-resource',resolveId(id){if(id==='/__agent-personality-resource-entry.js')return id},load(id){if(id==='/__agent-personality-resource-entry.js')return entry},configureServer(server){server.middlewares.use('/__agent-personality-resource',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__agent-personality-resource','<html><body><div id="root"></div><script type="module" src="/__agent-personality-resource-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
