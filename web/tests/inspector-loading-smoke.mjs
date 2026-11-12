import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {DeferredPanel} from '/src/components/ui/DeferredPanel.tsx'
import {ContextAuditPanel} from '/src/components/inspector/ContextAuditPanel.tsx'
import {emptySession,useChatStore} from '/src/store/chat.ts'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
const root=createRoot(document.getElementById('root'))
window.copy=t; window.reads=[]; window.modules=[]
api.contextAudit=(_s,_t,_b,signal)=>new Promise(resolve=>window.reads.push({signal,resolve}))
api.contextControl=async()=>({ok:true,data:null})
const loaders=[0,1].map(()=>()=>new Promise((resolve,reject)=>window.modules.push({resolve,reject})))
window.show=(index,locale='en')=>{setLocale(locale);root.render(index===2?React.createElement(ContextAuditPanel,{sessionId:'fixture',agentId:'child'}):React.createElement(DeferredPanel,{load:loaders[index],panelProps:{},skeleton:true,label:index?'Trace':'Context'}))}
window.finish=index=>window.modules[index].resolve({default:()=>React.createElement('p',null,'Ready '+index)})
window.revision=()=>useChatStore.setState(state=>({sessions:{...state.sessions,fixture:{...(state.sessions.fixture??emptySession()),contextRevision:(state.sessions.fixture?.contextRevision??0)+1}}}))
window.show(0)
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.clock.install()
 await page.goto(server.resolvedUrls.local[0]+'__inspector-loading')
 await page.getByRole('status').waitFor()
 assert.equal(await page.locator('.motion-safe\\:animate-pulse').count(),1)
 await page.clock.runFor(10001)
 await page.getByRole('alert').waitFor()
 assert.equal(await page.getByRole('status').count(),0)
 await page.evaluate(()=>window.show(1,'zh-CN'))
 await page.getByRole('status').waitFor()
 await page.evaluate(()=>window.finish(0))
 assert.equal(await page.getByText('Ready 0',{exact:true}).count(),0)
 await page.clock.runFor(10001)
 await page.getByRole('alert').waitFor()
 await page.evaluate(()=>window.finish(1))
 await page.getByText('Ready 1',{exact:true}).waitFor()
 await page.evaluate(()=>window.show(2))
 await page.waitForFunction(()=>window.reads.length===1)
 for(let i=0;i<5;i++){
  await page.evaluate(()=>window.revision())
  await page.evaluate(()=>new Promise(requestAnimationFrame))
 }
 assert.equal(await page.evaluate(()=>window.reads.length),1)
 assert.equal(await page.evaluate(()=>window.reads[0].signal.aborted),false)
 await page.getByText(await page.evaluate(()=>window.copy('ui.context.newRecords')),{exact:true}).waitFor()
 await page.evaluate(()=>window.reads[0].resolve({ok:false,message:'Controlled read failure'}))
 await page.getByRole('alert').filter({hasText:'Controlled read failure'}).waitFor()
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.inspector.ContextAuditPanel.920b5628b33a'))}).click()
 await page.waitForFunction(()=>window.reads.length===2)
 await page.evaluate(()=>window.show(1))
 await page.waitForFunction(()=>window.reads[1].signal.aborted)
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Inspector module timeout, late recovery, switch isolation and non-starving context reads passed')
},{entry:'/__inspector-loading-entry.js',vite:{plugins:[{
 name:'inspector-loading-fixture',resolveId(id){if(id==='/__inspector-loading-entry.js')return id},
 load(id){if(id==='/__inspector-loading-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__inspector-loading',async(_req,res,next)=>{
  try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__inspector-loading','<html><body><div id="root"></div><script type="module" src="/__inspector-loading-entry.js"></script></body></html>'))}catch(e){next(e)}
 })}
}]}})
