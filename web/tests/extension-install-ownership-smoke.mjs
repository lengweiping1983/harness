import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {SkillsPage} from '/src/components/overlays/EscPanel.tsx'
import {setLocale} from '/src/i18n/index.ts'
import {useAppStore} from '/src/store/app.ts'
import '/src/styles.css'
window.receipts=[]
useAppStore.setState({activeSessionId:'A',toast:value=>window.receipts.push(value)})
window.session=value=>useAppStore.setState({activeSessionId:value})
window.locale=setLocale
setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(SkillsPage))
`
const html='<html><body><div id="root"></div><script type="module" src="/__extension-install-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage()
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 let first=true,releaseRead,readEntered,releaseWrite,writeEntered,writes=0
 const readHeld=new Promise(resolve=>{readEntered=resolve})
 const writeHeld=new Promise(resolve=>{writeEntered=resolve})
 await page.route('**/api/v1/plugins/skills?*',async route=>{
  if(first){first=false;readEntered();await new Promise(resolve=>{releaseRead=resolve})}
  try{await route.fulfill({json:{enabled:true,skills:[]}})}catch(error){if(!route.request().failure())throw error}
 })
 await page.route('**/api/v1/plugins/skills/install',async route=>{
  writes++
  if(writes===1){writeEntered();await new Promise(resolve=>{releaseWrite=resolve})}
  await route.fulfill({json:{ok:true}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__extension-install')
 await readHeld
 const aborted=page.waitForEvent('requestfailed',{predicate:request=>request.url().includes('/plugins/skills?session_id=A')})
 const loaded=page.waitForResponse(response=>response.url().includes('/plugins/skills?session_id=B'))
 await page.evaluate(()=>window.session('B'));await aborted;releaseRead();await loaded
 const disclosure=page.getByRole('button',{name:'Install a skill from a local directory',exact:true})
 await disclosure.click()
 const path=page.getByRole('textbox',{name:'Skill catalog path',exact:true})
 await path.fill('/old-write')
 await page.getByRole('button',{name:'Install',exact:true}).click();await writeHeld
 const current=page.waitForResponse(response=>response.url().includes('/plugins/skills?session_id=C'))
 await page.evaluate(()=>window.session('C'));await current
 await disclosure.click();await path.waitFor()
 const applied=page.waitForResponse(response=>response.url().endsWith('/plugins/skills/install'))
 const rechecked=page.waitForResponse(response=>response.url().includes('/plugins/skills?session_id=C'))
 releaseWrite();await(await applied).finished();await rechecked
 await page.waitForFunction(()=>document.querySelector('input[aria-label="Skill catalog path"]')?.disabled===false)
 assert.equal(await path.isVisible(),true,'Old installation must not close the current scope form')
 assert.equal(writes,1,'Accepted writes must not be replayed after a scope change')
 assert.deepEqual(await page.evaluate(()=>window.receipts),[])
 await path.fill('/current-write')
 const saved=page.waitForResponse(response=>response.url().endsWith('/plugins/skills/install'))
 await page.getByRole('button',{name:'Install',exact:true}).click();await(await saved).finished()
 await path.waitFor({state:'hidden'})
 assert.equal(writes,2)
 assert.equal(await page.evaluate(()=>window.receipts.length),1,'Current installation publishes one receipt')
 assert.deepEqual(errors,[])
 console.log('Extension install: obsolete reads abort, accepted writes finish once, stale callbacks do not close current forms, current success closes normally')
},{entry:'/__extension-install-entry.js',vite:{plugins:[{name:'extension-install-fixture',resolveId(id){if(id==='/__extension-install-entry.js')return id},load(id){if(id==='/__extension-install-entry.js')return entry},configureServer(server){server.middlewares.use('/__extension-install',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__extension-install',html))}catch(error){next(error)}})}}]}})
