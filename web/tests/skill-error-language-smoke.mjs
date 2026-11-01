import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import '/@vite/client'
import React from 'react'
import {createRoot} from 'react-dom/client'
import {SkillAuditPanel} from '/src/components/inspector/SkillAuditPanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(SkillAuditPanel,{sessionId:'fixture',through:7}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();let reads=0;const errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)})
 await page.route('**/api/v1/sessions/fixture/context/skills*',route=>{reads++;return route.fulfill({status:200,body:'not json',contentType:'application/json'})})
 await page.goto(server.resolvedUrls.local[0]+'__skill-error');await page.locator('summary').click();await page.getByRole('alert').waitFor()
 for(const locale of ['en','zh-CN','en']) for(const theme of ['light','dark']) for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
  const expected=await page.evaluate(()=>window.copy('ui.http.invalidResponse'))
  await page.waitForFunction(expected=>document.querySelector('[role=alert]')?.firstChild?.textContent===expected,expected);assert.equal(reads,1)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Skill audit: saved protocol error changes language without refetching')

},{entry:'/__skill-error-entry.js',vite:{plugins:[{name:'skill-error-language-fixture',resolveId(id){if(id==='/__skill-error-entry.js')return id},load(id){if(id==='/__skill-error-entry.js')return entry},configureServer(server){server.middlewares.use('/__skill-error',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__skill-error','<html><body><div id="root"></div><script type="module" src="/__skill-error-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
