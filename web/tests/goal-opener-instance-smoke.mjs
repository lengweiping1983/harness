import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {useSessionPanelOpener} from '/src/hooks/useSessionPanelOpener.ts'
import {useAppStore} from '/src/store/app.ts'
let release;window.calls=0
useAppStore.setState({activeSessionId:null,newSessionRevision:1,materializeSession:()=>{window.calls++;return new Promise(resolve=>release=()=>{useAppStore.setState({activeSessionId:'owner'});resolve(true)})}})
function Fixture({owner}){const {request,open}=useSessionPanelOpener(owner);return React.createElement('div',null,React.createElement('button',{onClick:()=>{window.opening=open()}},'Open'),React.createElement('output',null,JSON.stringify(request)))}
const root=createRoot(document.getElementById('root'));window.show=owner=>root.render(React.createElement(Fixture,{key:owner??'empty',owner}));window.hide=()=>root.render(null);window.release=()=>release();window.change=()=>useAppStore.setState({newSessionRevision:2});window.show(null)
`
await browserScenario(async({server,browser})=>{
 for(const scenario of ['normal','remounted','changed']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0]+'__goal-opener-instance')
  await page.getByRole('button',{name:'Open',exact:true}).click()
  await page.waitForFunction(()=>window.calls===1)
  if(scenario==='remounted'){
   await page.evaluate(()=>window.hide());await page.locator('output').waitFor({state:'detached'})
   await page.evaluate(()=>window.show('owner'));await page.locator('output').waitFor()
  }
  if(scenario==='changed')await page.evaluate(()=>window.change())
  await page.evaluate(()=>window.release())
  await page.evaluate(()=>window.opening)
  if(scenario==='normal')await page.waitForFunction(()=>document.querySelector('output').textContent.includes('"serial":1'))
  else assert.equal(await page.locator('output').innerText(),'null')
  assert.equal(await page.evaluate(()=>window.calls),1)
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Goal opener: original mount only, late accepted creation never opens remounted or revised UI')
},{entry:'/__goal-opener-instance-entry.js',vite:{plugins:[{
 name:'goal-opener-instance-fixture',resolveId(id){if(id==='/__goal-opener-instance-entry.js')return id},load(id){if(id==='/__goal-opener-instance-entry.js')return entry},configureServer(server){server.middlewares.use('/__goal-opener-instance',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__goal-opener-instance','<html><body><div id="root"></div><script type="module" src="/__goal-opener-instance-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
