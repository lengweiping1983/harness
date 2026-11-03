import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {CollaborationButton} from '/src/components/shared/CollaborationButton.tsx'
import {collaboration} from '/src/lib/collaboration.ts'
import {ResourceError} from '/src/lib/resourceError.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.writes=[];window.readWaiters=[];window.holdRead=false;window.reads=0
window.failNextRead=new URLSearchParams(location.search).get('mode')==='read-throw'
window.state={origin:'https://sharing.example',access:{session_id:'owner',owner:'local-owner',revision:1,members:{'local-owner':['manage']},profiles:{}},invitations:[{id:'pending',role:'collaborator',status:'pending',expires_at:123}]}
collaboration.state=async(_session,signal)=>{window.lastReadSignal=signal;window.reads++;if(window.failNextRead){window.failNextRead=false;throw new ResourceError({key:'ui.http.invalidResponse'})}if(window.holdRead)await new Promise(resolve=>window.readWaiters.push(resolve));return {ok:true,data:window.state}}
collaboration.invite=(id,role)=>new Promise((resolve,reject)=>window.writes.push({kind:'invite',id,role,resolve,reject}))
collaboration.approve=(id,invitation,revision)=>new Promise((resolve,reject)=>window.writes.push({kind:'approve',id,invitation,revision,resolve,reject}))
window.finish=(index,success)=>{const write=window.writes[index];if(!success){write.reject(new ResourceError({key:'ui.http.invalidResponse'}));return}write.resolve({ok:true,data:write.kind==='invite'?{id:'created',token:'fixture.created.signature',role:write.role,expires_at:123}:window.state.access})}
window.releaseReads=()=>{window.holdRead=false;window.readWaiters.splice(0).forEach(resolve=>resolve())}
function Fixture(){const [open,setOpen]=React.useState(false);return React.createElement(CollaborationButton,{sessionId:'owner',open,onOpen:()=>setOpen(true),onClose:()=>setOpen(false)})}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['invite','approve','throw','read-throw','unmount','poll-race']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  if(mode==='poll-race'){await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))}
  await page.goto(server.resolvedUrls.local[0]+'__collaboration-operation?mode='+mode)
  const copy=key=>page.evaluate(key=>window.copy(key),key)
  const button=async key=>page.getByRole('button',{name:await copy(key),exact:true})
  await (await button('ui.collaboration.invite')).click()
  if(mode==='read-throw'){
   await page.getByRole('alert').filter({hasText:await copy('ui.http.invalidResponse')}).waitFor()
   await (await button('ui.collaboration.refresh')).click()
  }
  const create=await button('ui.collaboration.create');await create.waitFor();await page.waitForFunction(()=>document.querySelector('select')!==null)
  if(mode==='poll-race'){
   await page.evaluate(()=>{window.holdRead=true})
   await page.clock.runFor(5000)
   await page.waitForFunction(()=>window.readWaiters.length===1)
  }
  const action=mode==='approve'?await button('ui.collaboration.approve'):create
  await action.evaluate(node=>{node.click();node.click()})
  await page.waitForFunction(()=>window.writes.length>0)
  assert.equal(await page.evaluate(()=>window.writes.length),1)
  if(mode==='poll-race'){
   assert.equal(await page.evaluate(()=>window.lastReadSignal.aborted),true)
   const reads=await page.evaluate(()=>window.reads)
   await page.clock.runFor(10000)
   assert.equal(await page.evaluate(()=>window.reads),reads,'write pauses scheduled observation')
   await page.evaluate(()=>window.releaseReads())
  }
  if(mode==='unmount'){
   const reads=await page.evaluate(()=>window.reads)
   await (await button('ui.collaboration.close')).click()
   await page.getByRole('dialog').waitFor({state:'detached'})
   await page.evaluate(async()=>{window.finish(0,true);await Promise.resolve();await Promise.resolve()})
   assert.equal(await page.evaluate(()=>window.reads),reads)
   await (await button('ui.collaboration.invite')).click()
   await (await button('ui.collaboration.create')).waitFor()
   assert.equal(await (await button('ui.collaboration.create')).isDisabled(),false)
   assert.equal(await page.evaluate(()=>window.writes.length),1)
  }else if(mode==='throw'){
   await page.evaluate(()=>window.finish(0,false))
   for(const locale of ['en','zh-CN','en']){
    await page.evaluate(locale=>window.locale(locale),locale)
    await page.getByRole('alert').filter({hasText:await copy('ui.http.invalidResponse')}).waitFor()
    assert.equal(await (await button('ui.collaboration.create')).isDisabled(),false)
    assert.equal(await page.evaluate(()=>window.writes.length),1)
   }
   await (await button('ui.collaboration.create')).click();await page.waitForFunction(()=>window.writes.length===2)
   await page.evaluate(()=>window.finish(1,true))
   await page.getByRole('textbox',{name:await copy('ui.collaboration.link')}).waitFor()
  }else{
   for(const locale of ['en','zh-CN','en']){
    await page.evaluate(locale=>window.locale(locale),locale)
    assert.equal(await (await button('ui.collaboration.create')).isDisabled(),true)
    assert.equal(await page.evaluate(()=>window.writes.length),1)
   }
   await page.evaluate(()=>{window.holdRead=true;window.finish(0,true)})
   await page.waitForFunction(()=>window.readWaiters.length>0)
   assert.equal(await (await button('ui.collaboration.create')).isDisabled(),true)
   await (await button('ui.collaboration.create')).evaluate(node=>node.click())
   assert.equal(await page.evaluate(()=>window.writes.length),1)
   await page.evaluate(()=>window.releaseReads())
   await page.waitForFunction(label=>[...document.querySelectorAll('button')].some(node=>node.textContent===label&&!node.disabled),await copy('ui.collaboration.create'))
   if(mode!=='approve')assert.equal(await page.getByRole('textbox',{name:await copy('ui.collaboration.link')}).inputValue(),'https://sharing.example/shared#token=fixture.created.signature')
  }
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Collaboration: synchronous duplicate admission blocked, lock includes readback, thrown write/read resources recover without replay across languages')
},{entry:'/__collaboration-operation-entry.js',vite:{plugins:[{name:'collaboration-operation',resolveId(id){if(id==='/__collaboration-operation-entry.js')return id},load(id){if(id==='/__collaboration-operation-entry.js')return entry},configureServer(server){server.middlewares.use('/__collaboration-operation',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__collaboration-operation','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__collaboration-operation-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
