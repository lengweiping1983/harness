import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useRef,useState} from 'react'
import {createRoot} from 'react-dom/client'
import {CapabilityPicker} from '/src/components/chat/CapabilityPicker.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useChatStore,emptySession} from '/src/store/chat.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
useAppStore.setState({activeSessionId:'A'});useChatStore.setState({sessions:{A:{...emptySession(),agentMount:new URLSearchParams(location.search).get('action')==='remove'?{revision:1,available:true,agent:'original',agent_type:'agent'}:null}}})
window.copy=t;window.effects=[];window.locale=setLocale;window.switchTo=id=>useAppStore.setState({activeSessionId:id})
window.setMenu=()=>{}
window.mount=()=>useChatStore.getState().sessions.A.agentMount
function Fixture(){const anchor=useRef(null),[open,setOpen]=useState(false);window.setMenu=setOpen;return React.createElement(React.Fragment,null,React.createElement('button',{ref:anchor,onClick:()=>setOpen(true)},'Open'),React.createElement(CapabilityPicker,{capabilityKind:'agents',sessionId:'A',draft:'',onChange:()=>{},focusEditor:()=>window.effects.push('focus'),open,setOpen:next=>{window.effects.push('open:'+next);setOpen(next)},menuAnchor:anchor}))}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const action of ['select','remove'])for(const language of ['en','zh-CN'])for(const mode of (action==='select'?['normal','switch','roundtrip','closed','reopened']:['normal','switch','roundtrip']))for(const failure of [false,true]){
  const page=await browser.newPage();let release,enter,writes=0;const gate=new Promise(r=>release=r),held=new Promise(r=>enter=r)
  await page.route('**/api/v1/**',async route=>{
   if(route.request().method()==='POST'){writes++;enter();await gate;return route.fulfill(failure?{status:500,json:{message:'Original mount failure'}}:{json:{mount:{revision:2,available:action!=='remove',agent:action==='remove'?null:'original',agent_type:'agent',display_name:'Original Agent'}}})}
   if(route.request().url().includes('view=picker'))return route.fulfill({json:{agents:[{name:'original',display_name:'Original Agent',agent_type:'agent',description:'Original description'}],capabilities:{persistent_team_selection:true}}})
   return route.fulfill({status:404,body:''})
  })
  await page.goto(server.resolvedUrls.local[0]+'__capability-focus?action='+action);await page.evaluate(language=>window.locale(language),language)
  if(action==='select'){await page.getByRole('button',{name:'Open',exact:true}).click();await page.getByRole('option').first().click()}
  else {const remove=page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.composerAgents.unmount')),exact:true});await remove.focus();await remove.press('Enter')}
  await held
  await page.evaluate(mode=>{window.effects=[];if(mode==='switch'||mode==='roundtrip')window.switchTo('B');if(mode==='roundtrip')window.switchTo('A');if(mode==='closed'||mode==='reopened')window.setMenu(false)},mode)
  if(mode==='closed'||mode==='reopened')await page.getByRole('option').first().waitFor({state:'detached'})
  if(mode==='reopened'){await page.evaluate(()=>window.setMenu(true));await page.getByRole('option').first().waitFor()}
  const response=page.waitForResponse(response=>response.request().method()==='POST')
  release()
  if(mode!=='closed'&&mode!=='reopened')await page.waitForFunction(()=>window.mount()?.revision===2||document.querySelector('[role="option"]')?.disabled===false||window.effects.includes('focus')||document.querySelector('[role="alert"]')||Array.from(document.querySelectorAll('button')).some(node=>node.getAttribute('aria-label')===window.copy('ui.composerAgents.unmount')&&!node.disabled))
  if(mode==='closed'||mode==='reopened'){await response;await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))}
  if(mode==='normal'&&!(action==='remove'&&failure))await page.waitForFunction(()=>window.effects.includes('focus'))
  assert.deepEqual(await page.evaluate(()=>window.effects),mode==='normal'?(action==='select'?['open:false','focus']:failure?[]:['focus']):[],action+' '+mode+' '+failure)
  if(!failure)assert.equal(await page.evaluate(()=>window.mount().agent),action==='remove'?null:'original')
  assert.equal(writes,1);await page.close()
 }
 console.log('Capability receipts preserve original mount but isolate menu and focus after switch or A-B-A, both languages and outcomes')
},{entry:'/__capability-focus-entry.js',vite:{plugins:[{name:'capability-focus-fixture',resolveId(id){if(id==='/__capability-focus-entry.js')return id},load(id){if(id==='/__capability-focus-entry.js')return entry},configureServer(server){server.middlewares.use('/__capability-focus',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__capability-focus','<html><body><div id="root"></div><script type="module" src="/__capability-focus-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
