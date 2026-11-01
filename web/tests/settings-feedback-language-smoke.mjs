import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {SkillsPage} from '/src/components/overlays/EscPanel.tsx'
import {CapabilitiesPanel} from '/src/components/overlays/CapabilitiesPanel.tsx'
import {ChatGPTLogin} from '/src/components/overlays/ChatGPTLogin.tsx'
import {PersonalityPanel} from '/src/components/overlays/PersonalityPanel.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
useAppStore.setState({activeSessionId:'fixture'})
const view=new URLSearchParams(location.search).get('view'),noop=()=>{}
const content=view.startsWith('esc')?React.createElement(SkillsPage):view==='capabilities'?React.createElement(CapabilitiesPanel):view==='chatgpt'?React.createElement(ChatGPTLogin,{onStatus:noop}):React.createElement(PersonalityPanel)
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,content,React.createElement(Toaster)))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['esc-read','esc-refresh','capabilities','chatgpt','personality']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  let written=false
  await page.route('**/api/v1/**',route=>{
   const request=route.request();requests.push(request.method())
   if(view==='esc-refresh'){
    if(request.method()==='POST'){written=true;return route.fulfill({json:{enabled:false}})}
    if(!written)return route.fulfill({json:{enabled:true,skills:[{name:'FixtureSkill',description:'Original description 原文',enabled:true,plugin_enabled:true}]}})
   }
   return route.fulfill({status:200,body:'not json',contentType:'application/json'})
  })
  await page.goto(server.resolvedUrls.local[0]+'__settings-feedback?view='+view)
  if(view==='esc-refresh')await page.getByRole('switch',{name:await page.evaluate(()=>window.copy('ui.catalog.disableNamed',{name:'FixtureSkill'})),exact:true}).click()
  const expected=()=>page.evaluate(view=>{
   const invalid=window.copy('ui.http.invalidResponse')
   return view==='personality'?window.copy('ui.common.feedbackPair',{first:invalid,second:invalid}):view==='esc-refresh'?window.copy('ui.esc.operationAppliedRefreshFailed',{reason:invalid}):invalid
  },view)
  await page.getByText(await expected(),{exact:true}).first().waitFor()
  const before=requests.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await page.getByText(await expected(),{exact:true}).first().waitFor()
   if(view==='esc-refresh')await page.getByText(await page.evaluate(()=>window.copy('ui.components.overlays.EscPanel.dad663a8630a',{p0:'FixtureSkill'})),{exact:true}).waitFor()
   if(view==='chatgpt')assert.equal(requests.length,before)
   if(view==='personality'&&locale==='en')assert.ok(!(await expected()).includes('；'))
  }
  assert.equal(requests.filter(method=>method!=='GET').length,view==='esc-refresh'?1:0)
  assert.deepEqual(errors,[],view);await page.close();console.log(view+': feedback and receipts follow language without repeating writes')
 }
},{entry:'/__settings-feedback-entry.js',vite:{plugins:[{
 name:'settings-feedback-fixture',resolveId(id){if(id==='/__settings-feedback-entry.js')return id},load(id){if(id==='/__settings-feedback-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__settings-feedback',async(_req,res,next)=>{
  try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__settings-feedback','<html><body><div id="root"></div><script type="module" src="/__settings-feedback-entry.js"></script></body></html>'))}catch(error){next(error)}
 })},
}]}})
