import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {SkillsPage} from '/src/components/overlays/EscPanel.tsx'
import {setLocale} from '/src/i18n/index.ts'
import {useAppStore} from '/src/store/app.ts'
import '/src/styles.css'
const root=createRoot(document.getElementById('root'))
window.receipts=[]
useAppStore.setState({activeSessionId:'A',toast:value=>window.receipts.push(value)})
window.locale=setLocale
window.session=value=>useAppStore.setState({activeSessionId:value})
window.mount=()=>root.render(React.createElement(SkillsPage))
window.clear=()=>root.render(null)
setLocale('en');window.mount()
`
const html='<html><body><div id="root"></div><script type="module" src="/__extension-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage()
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 let enabled=true,writes=0,reads=0,release,entered
 const held=new Promise(resolve=>{entered=resolve})
 await page.route('**/api/v1/plugins/skills?*',route=>{reads++;return route.fulfill({json:{enabled:true,skills:[{name:'fixture-skill',description:'fixture',enabled,source:'bundled'}]}})})
 await page.route('**/api/v1/plugins/skills/fixture-skill/enabled',async route=>{
  writes++;entered();await new Promise(resolve=>{release=resolve});enabled=false
  return route.fulfill({json:{ok:true}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__extension')
 const toggle=page.getByRole('switch')
 await toggle.waitFor();await toggle.click();await held
 for(const language of ['zh-CN','en']){
  const loaded=page.waitForResponse(response=>response.url().includes('/plugins/skills?'))
  await page.evaluate(value=>window.locale(value),language);await loaded;await toggle.waitFor()
 }
 for(const session of ['B','A']){
  const loaded=page.waitForResponse(response=>response.url().includes('/plugins/skills?'))
  await page.evaluate(value=>window.session(value),session);await loaded;await toggle.waitFor()
 }
 assert.equal(await toggle.isDisabled(),true,'Scope changes must retain the accepted write lock')
 assert.equal(writes,1)
 const before=reads
 const saved=page.waitForResponse(response=>response.url().endsWith('/fixture-skill/enabled'))
 const refreshed=page.waitForResponse(response=>response.url().includes('/plugins/skills?'))
 release();await(await saved).finished();await refreshed
 await page.waitForFunction(()=>document.querySelector('[role="switch"]')?.disabled===false)
 assert.equal(await toggle.isChecked(),false,'Completion must re-read the current authoritative directory')
 assert.equal(reads,before+1)
 assert.deepEqual(await page.evaluate(()=>window.receipts),[],'A returning scope must not receive an old receipt')
 const heldAgain=new Promise(resolve=>{entered=resolve})
 await toggle.click();await heldAgain
 await page.waitForFunction(()=>document.querySelector('[role="switch"]')?.disabled===true)
 const count=reads
 await page.evaluate(()=>window.clear())
 const second=page.waitForResponse(response=>response.url().endsWith('/fixture-skill/enabled'))
 release();await(await second).finished()
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
 assert.equal(reads,count,'Unmounted writes must not restart directory observations')
 assert.deepEqual(await page.evaluate(()=>window.receipts),[])
 assert.deepEqual(errors,[])
 console.log('Extensions: language/session ABA retains write ownership, re-reads current state, suppresses obsolete receipts and stops unmounted observation')
},{entry:'/__extension-entry.js',vite:{plugins:[{name:'extension-fixture',resolveId(id){if(id==='/__extension-entry.js')return id},load(id){if(id==='/__extension-entry.js')return entry},configureServer(server){server.middlewares.use('/__extension',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__extension',html))}catch(error){next(error)}})}}]}})
