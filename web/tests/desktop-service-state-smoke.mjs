import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {DesktopServiceStatus} from '/src/components/desktop/DesktopServiceStatus.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en');window.calls=[];window.pending=[];window.boots=0;window.restored=0
window.addEventListener('harness-service-restored',()=>window.restored++)
useAppStore.setState({boot:async()=>{window.boots++}})
const listeners=new Set()
window.emit=state=>listeners.forEach(listener=>listener(state))
window.harnessDesktop={onServiceState:listener=>{listeners.add(listener);return()=>listeners.delete(listener)},browser:async action=>{
 window.calls.push(action)
 if(action==='service_state')return {status:'stopped'}
 return new Promise((resolve,reject)=>window.pending.push({resolve,reject}))
}}
const root=createRoot(document.getElementById('root'))
window.show=id=>root.render(React.createElement(DesktopServiceStatus,{key:id}));window.show('initial')
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__desktop-service')
 const reconnect=()=>page.getByRole('button')
 await reconnect().waitFor()
 await reconnect().click()
 await page.waitForFunction(()=>window.pending.length===1)
 await reconnect().dispatchEvent('click')
 assert.equal(await page.evaluate(()=>window.pending.length),1)
 await page.evaluate(()=>window.pending[0].resolve({status:'running'}))
 await page.getByRole('alert').waitFor({state:'detached'})
 assert.deepEqual(await page.evaluate(()=>[window.boots,window.restored]),[1,1])
 for(const outcome of ['resolve','reject']) {
  await page.evaluate(()=>window.emit('stopped'))
  await reconnect().click()
  const count=await page.evaluate(()=>window.pending.length)
  await page.evaluate(()=>window.emit('running'))
  await page.getByRole('alert').waitFor({state:'detached'})
  await page.evaluate(({count,outcome})=>{
   const operation=window.pending[count-1]
   if(outcome==='reject')operation.reject(new Error('Older restart failure'))
   else operation.resolve({status:'stopped'})
  },{count,outcome})
  await page.evaluate(()=>new Promise(requestAnimationFrame))
  assert.equal(await page.getByRole('alert').count(),0)
 }
 assert.deepEqual(await page.evaluate(()=>[window.boots,window.restored]),[3,3])
 await page.evaluate(()=>window.emit('stopped'))
 await reconnect().click()
 await page.evaluate(()=>window.pending.at(-1).reject(new Error('Current restart failure')))
 await page.getByRole('button',{name:'Reconnect service',exact:true}).waitFor({state:'visible'})
 for(const locale of ['en','zh-CN','en']) {
  await page.evaluate(locale=>window.locale(locale),locale)
  const expected=await page.evaluate(()=>window.copy('browser.desktop.serviceRetryFailed'))
  await page.getByText(expected,{exact:true}).waitFor()
  for(const theme of ['light','dark'])for(const width of [390,1440]) {
   await page.setViewportSize({width,height:900})
   await page.evaluate(theme=>document.documentElement.classList.toggle('dark',theme==='dark'),theme)
   assert.equal(await page.getByText(expected,{exact:true}).isVisible(),true)
  }
 }
 assert.equal(await page.evaluate(()=>window.calls.filter(action=>action==='restart_backend').length),4)
 await reconnect().click()
 const old=await page.evaluate(()=>window.pending.length-1)
 await page.evaluate(()=>window.show('new-instance'))
 await page.getByRole('button',{name:'Reconnect service',exact:true}).waitFor()
 await page.evaluate(old=>window.pending[old].reject(new Error('Unmounted restart failure')),old)
 await page.evaluate(()=>new Promise(requestAnimationFrame))
 await page.getByText(await page.evaluate(()=>window.copy('browser.desktop.servicePreserved')),{exact:true}).waitFor()
 assert.deepEqual(errors,[])
 console.log('Desktop service restart acknowledgement restores the workspace and ignores stale results across status events and mounted instances; bilingual failure rendering does not replay restart')
},{entry:'/__desktop-service-entry.js',vite:{plugins:[{name:'desktop-service-state-fixture',resolveId(id){if(id==='/__desktop-service-entry.js')return id},load(id){if(id==='/__desktop-service-entry.js')return entry},configureServer(server){server.middlewares.use('/__desktop-service',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__desktop-service','<html><body><div id="root"></div><script type="module" src="/__desktop-service-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
