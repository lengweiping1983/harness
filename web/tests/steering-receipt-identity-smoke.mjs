import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import '/@vite/client'
import React from 'react'
import {createRoot} from 'react-dom/client'
import {useDraftStore} from '/src/store/drafts.ts'
import {useAppStore} from '/src/store/app.ts'
import {SteeringDock} from '/src/components/chat/SteeringDock.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
useAppStore.setState({activeSessionId:'fixture'});window.switchTo=id=>useAppStore.setState({activeSessionId:id})
window.locale=setLocale;window.copy=t;window.restored=0;window.draft=()=>useDraftStore.getState().bySession.fixture?.text;setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(SteeringDock,{sessionId:'fixture',receipts:[{request_id:'request',turn:1,accepted_seq:7,status:'unapplied',text:'Original 原文'}],onRestored:()=>{window.restored++}}))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['wrong-request','wrong-turn','wrong-seq','truncated','valid','valid-switch','valid-roundtrip']){
 const page=await browser.newPage();let reads=0;const errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)})
 await page.route('**/api/v1/sessions/fixture/steering/request',async route=>{reads++;if(mode.startsWith('valid-'))await page.evaluate(roundtrip=>{window.switchTo('other');if(roundtrip)window.switchTo('fixture')},mode==='valid-roundtrip');return route.fulfill({json:{receipt:{request_id:mode==='wrong-request'?'foreign':'request',turn:mode==='wrong-turn'?2:1,accepted_seq:mode==='wrong-seq'?8:7,text_truncated:mode==='truncated',status:'unapplied',text:'Restored original 原文',accepted_at:1,applied_seq:null,reason:'',team_delivered:false,delivery_seq:null}}})})
 await page.goto(server.resolvedUrls.local[0]+'__steering');await page.getByRole('button').click()
 for(const locale of ['en','zh-CN','en']) for(const theme of ['light','dark']) for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.dataset.theme=theme},{locale,theme})
  if(mode.startsWith('valid')){await page.waitForFunction(()=>window.draft()==='Restored original 原文');assert.equal(await page.evaluate(()=>window.restored),mode==='valid'?1:0)}else {const expected=await page.evaluate(()=>window.copy('ui.components.chat.SteeringDock.d590a5909a83'));await page.getByText(expected,{exact:true}).waitFor();assert.equal(await page.evaluate(()=>window.restored),0)}
  assert.equal(await page.evaluate(()=>window.draft()),mode.startsWith('valid')?'Restored original 原文':undefined);assert.equal(reads,1);assert.ok(await page.getByText('Original 原文',{exact:true}).isVisible())
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Steering error: same saved state changes language without refetching or restoring; original text preserved: '+mode)
 }

},{entry:'/__steering-entry.js',vite:{plugins:[{name:'steering-language-fixture',resolveId(id){if(id==='/__steering-entry.js')return id},load(id){if(id==='/__steering-entry.js')return entry},configureServer(server){server.middlewares.use('/__steering',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__steering','<html><body><div id="root"></div><script type="module" src="/__steering-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
