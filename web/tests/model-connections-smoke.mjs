import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry = `
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ModelConnections} from '/src/components/overlays/ModelConnections.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
function Fixture(){const [shown,setShown]=useState(true);window.show=setShown;window.locale=setLocale;window.copy=t;return shown?React.createElement(ModelConnections):null}
setLocale('en');createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(15000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 const provider=name=>({id:'supplier',type:'custom',name,connections:[{id:'endpoint',name:'Gateway 原文',base_url:'https://example.test/v1',protocol:'openai-completions',enabled:true,auth:{type:'api_key',env:{},configured:false},models:[{id:'model',model:'remote',name:'Model 原文',enabled:true,input:['text']}],keys:[{id:'key',mask:'******3blr',enabled:true,available:true,current:false,in_use:false}]}]})
 const routing=name=>({version:2,revision:name,providers:[provider(name)],models:[],current:null,cycle:1})
 let current=routing('Initial 原文'),writes=0,release,entered
 const held=new Promise(resolve=>{entered=resolve})
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url())
  if(url.pathname==='/api/v1/connection/providers')return route.fulfill({json:{providers:[{id:'custom',name:'Custom API',api_key:true,oauth:false,base_url:'',env:[],models:[]}]}})
  if(url.pathname==='/api/v1/codex-gateway')return route.fulfill({json:{enabled:false,auto_rotate:true,current_account:'',accounts:[],threshold:95,waiting:0}})
  if(url.pathname==='/api/v1/models')return route.fulfill({json:{models:[],active:'',providers:[]}})
  if(url.pathname==='/api/v1/connection'){
   if(route.request().method()==='GET')return route.fulfill({json:current})
   writes++;const payload=route.request().postDataJSON();assert.equal(payload.providers[0].connections[0].keys[0].value,undefined);assert.equal(payload.revision,current.revision);assert.equal(payload.providers[0].connections[0].name,'Gateway 原文');assert.equal(payload.providers[0].connections[0].protocol,'openai-responses')
   entered();await new Promise(resolve=>{release=resolve});return route.fulfill({json:{config:{},routing:routing('Obsolete 原文')}})
  }
  errors.push('Unexpected request '+url.pathname);return route.fulfill({status:500,json:{error:'Unexpected fixture request'}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__connections')
 await page.getByText('Initial 原文',{exact:true}).waitFor()
 for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(async({language,theme})=>{await window.locale(language);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{language,theme})
  const labels=await page.evaluate(()=>({title:window.copy('ui.providers.title'),summary:window.copy('ui.providers.summary',{state:window.copy('ui.providers.state.configured'),connections:1,models:1})}))
  await page.getByRole('heading',{name:labels.title,exact:true}).waitFor();await page.getByText(labels.summary,{exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1),false)
 }
 await page.getByRole('button',{name:'Edit',exact:true}).click()
 const name=page.getByLabel('Provider name',{exact:true}).first();await name.fill('Draft 原文')
 for(const language of ['en','zh-CN','en']){
  await page.evaluate(language=>window.locale(language),language)
  const labels=await page.evaluate(()=>Object.fromEntries(['protocol','endpoint','authentication','connectionName','supplierName'].map(key=>[key,window.copy('ui.providers.'+key)])))
  const protocol=page.getByRole('combobox',{name:labels.protocol,exact:true});await protocol.waitFor({state:'visible'})
  await page.getByRole('combobox',{name:labels.authentication,exact:true}).waitFor({state:'visible'})
  assert.equal(await page.getByRole('textbox',{name:labels.supplierName,exact:true}).inputValue(),'Draft 原文')
  assert.equal(await protocol.inputValue(),'openai-completions')
  assert.equal(await page.getByLabel(labels.connectionName,{exact:true}).count(),0)
  assert.equal(await protocol.evaluate((element,endpointLabel)=>{
   const advanced=element.closest('details').querySelector('details')
   const endpoint=[...element.closest('form').querySelectorAll('label')].find(label=>label.textContent.includes(endpointLabel))
   return !advanced.open&&!advanced.contains(element)&&!!(element.compareDocumentPosition(endpoint)&Node.DOCUMENT_POSITION_FOLLOWING)
  },labels.endpoint),true)
 }
 await page.getByRole('combobox',{name:'API protocol',exact:true}).selectOption('openai-responses')

 await page.getByPlaceholder('******3blr',{exact:true}).waitFor()
 const save=page.getByRole('button',{name:'Save',exact:true});await save.click();await held
 await save.evaluate(element=>element.click());assert.equal(writes,1)
 await page.evaluate(()=>window.show(false));await page.getByRole('heading',{name:'Model providers',exact:true}).waitFor({state:'detached'});current=routing('Authoritative 原文');release()
 await page.evaluate(()=>window.show(true));await page.getByText('Authoritative 原文',{exact:true}).waitFor()
 assert.equal(await page.getByText('Obsolete 原文',{exact:true}).count(),0)
 assert.equal(writes,1);assert.deepEqual(errors,[])
 await page.close()
},{entry:'/__connections-entry.jsx',vite:{plugins:[{
 name:'provider-connections-fixture',resolveId(id){if(id==='/__connections-entry.jsx')return id},load(id){if(id==='/__connections-entry.jsx')return entry},
 configureServer(server){server.middlewares.use('/__connections',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__connections','<html><body><div id="root"></div><script type="module" src="/__connections-entry.jsx"></script></body></html>'))}catch(error){next(error)}})},
}]}})
