import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AgentDefinitionCreator} from '/src/components/overlays/AgentDefinitionCreator.tsx'
import {teamTemplate} from '/src/lib/teamTemplates.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
await setLocale('en');window.locale=setLocale;window.copy=t
window.writes=[];window.pending=[];window.refreshes=0;window.refreshPending=[]
const mode=new URLSearchParams(location.search).get('mode')
window.fetch=async(path,options={})=>{
 const method=options.method||'GET'
 if(method!=='GET'){
  const payload=JSON.parse(options.body)
  window.writes.push({path,method,payload})
  return new Promise(resolve=>window.pending.push((rejected=false)=>resolve(new Response(JSON.stringify(rejected?{code:'agent/invalid',error:'Fixture explicit rejection 原文'}:String(path).endsWith('/preview')?{agent:teamTemplate('delivery')}:method==='PATCH'?{updated:true,agent:payload,revision:'b'.repeat(64)}:{created:true,agent:payload}),{status:rejected?400:200,headers:{'Content-Type':'application/json'}}))))
 }
 return new Response(JSON.stringify(String(path).includes('/personalities')?{profiles:[]}:{runners:[],delegation_enabled:true}),{headers:{'Content-Type':'application/json'}})
}
window.mount=()=>{
 const seed=mode==='import'?null:{...teamTemplate('delivery'),...(mode==='edit'?{edit_revision:'a'.repeat(64),edit_scope:'user'}:{})}
 createRoot(document.getElementById('root')).render(React.createElement(AgentDefinitionCreator,{sessionId:null,seed,agents:[],initialKind:'team',refresh:async()=>{window.refreshes++;await new Promise(resolve=>window.refreshPending.push(resolve))}}))
}
window.mount()
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['create','edit','rejected-retry','import']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  try{
   await page.goto(server.resolvedUrls.local[0]+'__team-save-admission?mode='+mode)
   if(mode==='import'){
    await page.locator('input[type=file]').evaluate(input=>{
     const transfer=new DataTransfer();transfer.items.add(new File(['---\nname: fixture\n---\nFixture 原文'],'AGENTS.md',{type:'text/markdown'}));input.files=transfer.files
     input.dispatchEvent(new Event('change',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))
    })
   }else{
    const save=page.getByRole('dialog').getByRole('button',{name:await page.evaluate(mode=>window.copy(mode==='edit'?'ui.agent.author.editSave':'ui.agent.author.createOnly'),mode),exact:true})
    await save.waitFor()
    const blockedLabels=await page.evaluate(()=>['newTeam','discardDraft','editCopy'].map(key=>window.copy('ui.agent.author.'+key)))
    await save.evaluate((button,blockedLabels)=>{
     const dialog=button.closest('[role="dialog"]')
     button.click();button.click()
     for(const label of blockedLabels)Array.from(document.querySelectorAll('button')).find(candidate=>candidate.textContent===label)?.click()
     dialog.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))
    },blockedLabels)
   }
   await page.waitForFunction(()=>window.writes.length>0)
   assert.equal(await page.evaluate(()=>window.writes.length),1,mode+': same-event submissions must have one owner')
   if(mode!=='import'){
    assert.equal(await page.getByRole('dialog').count(),1,'Escape cannot close an accepted save')
    const write=await page.evaluate(()=>window.writes[0])
    assert.equal(write.method,mode==='edit'?'PATCH':'POST')
    assert.equal(write.payload.scope,'user')
    assert.deepEqual(write.payload.members.map(row=>row.access),['read','read','inherit','read'])
   }
   for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
    await page.setViewportSize({width,height:900});await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
    const label=await page.evaluate(mode=>window.copy(mode==='edit'?'ui.agent.author.editSave':mode==='import'?'ui.agent.author.newTeam':'ui.agent.author.createOnly'),mode)
    const button=mode==='import'?page.getByRole('button',{name:label,exact:true}):page.getByRole('dialog').getByRole('button',{name:label,exact:true})
    assert.ok(await button.isDisabled())
    await button.evaluate(button=>button.click())
    assert.equal(await page.evaluate(()=>window.writes.length),1)
   }
   if(mode==='rejected-retry'){
    await page.evaluate(()=>window.pending.shift()(true))
    await page.getByRole('alert').filter({hasText:'Fixture explicit rejection 原文'}).waitFor()
    const label=await page.evaluate(()=>window.copy('ui.agent.author.createOnly'))
    const save=page.getByRole('dialog').getByRole('button',{name:label,exact:true})
    await page.waitForFunction(label=>Array.from(document.querySelectorAll('button')).some(b=>b.textContent===label&&!b.disabled),label)
    await save.evaluate(button=>{button.click();button.click()})
    await page.waitForFunction(()=>window.writes.length===2)
    assert.equal(await page.evaluate(()=>window.writes.length),2,'Explicit rejection releases admission for one new attempt')
   }
   await page.evaluate(()=>window.pending.shift()())
   if(mode==='import'){
    await page.getByRole('dialog').waitFor()
    assert.equal(await page.evaluate(()=>window.refreshes),0)
    assert.equal(await page.evaluate(()=>window.writes[0].path),'/api/v1/plugins/agents/preview')
   }else{
    await page.waitForFunction(()=>window.refreshes===1)
    assert.equal(await page.getByRole('dialog').count(),0)
    const button=page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.agent.author.newTeam')),exact:true})
    assert.ok(await button.isDisabled(),'Admission remains owned through catalog refresh')
    await page.evaluate(()=>window.refreshPending.shift()())
    await page.waitForFunction(label=>Array.from(document.querySelectorAll('button')).some(b=>b.textContent===label&&!b.disabled),await page.evaluate(()=>window.copy('ui.agent.author.newTeam')))
   }
   assert.deepEqual(errors,[])
   console.log(mode+': one request, language-stable admission and normal settlement')
  }finally{await page.evaluate(()=>{window.pending.forEach(resolve=>resolve(true));window.refreshPending.forEach(resolve=>resolve())}).catch(()=>{});await page.close()}
 }
},{entry:'/__team-save-admission-entry.js',vite:{plugins:[{name:'team-save-admission',resolveId(id){if(id==='/__team-save-admission-entry.js')return id},load(id){if(id==='/__team-save-admission-entry.js')return entry},configureServer(server){server.middlewares.use('/__team-save-admission',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__team-save-admission','<html><body><div id="root"></div><script type="module" src="/__team-save-admission-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
