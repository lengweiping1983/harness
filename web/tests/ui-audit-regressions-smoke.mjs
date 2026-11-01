import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PluginMarketplaces} from '/src/components/overlays/PluginMarketplaces.tsx'
import {Composer} from '/src/components/chat/Composer.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useChatStore,emptySession} from '/src/store/chat.ts'
import {readGeneration} from '/src/lib/generation.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en')
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',resolved_cwd:'/fixture'}],models:[]})
const generation=readGeneration({request_id:'stale',phase:'waiting'})
window.prompt = pending=>useChatStore.setState({sessions:{fixture:{...emptySession(),blocks:pending?[{id:'permission',kind:'question',questionId:'permission',at:1,questions:[{id:'permission',question:'Approve?',options:[]}]}]:[]}}})
window.prompt(true)
window.locale=setLocale
function Fixture(){return React.createElement(React.Fragment,null,
 React.createElement(PluginMarketplaces,{onPreview:()=>{}}),
 React.createElement(Composer,{generation,running:true,queued:0,planMode:false,connection:'open',onSend:()=>{},onCancel:()=>{},onTogglePlan:()=>{}}))}
createRoot(document.getElementById('root')).render(React.createElement(React.StrictMode,null,React.createElement(Fixture)))
`
const html='<!doctype html><html><body><div id="root"></div><script type="module" src="/__audit-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
  const page=await browser.newPage({viewport:{width:390,height:900}})
  const errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>route.fulfill({json:{runs:[],permissions:{permission_policy:'ask',scope:'fixture',version:'a'.repeat(64),inherited:true}}}))
  await page.route('**/api/v1/connection',route=>route.fulfill({json:{version:2,revision:'fixture',providers:[],models:[],current:null,cycle:1}}))
  await page.route('**/api/v1/plugin-marketplaces',route=>route.fulfill({json:{marketplaces:[]}}))
  await page.goto(server.resolvedUrls.local[0]+'__audit')
  await page.getByRole('textbox',{name:'Input message',exact:true}).waitFor()
  for(const [locale,theme,width,label] of [['en','',390,'Model generation status'],['zh-CN','dark',1440,'模型生成状态']]){
    await page.setViewportSize({width,height:900})
    await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme}, {locale,theme})
    await page.evaluate(()=>window.prompt(false))
    await page.getByLabel(label,{exact:true}).waitFor()
    await page.evaluate(()=>window.prompt(true))
    await page.getByLabel(label,{exact:true}).waitFor({state:'detached'})
    assert.equal(await page.getByRole('alert').count(),0,'StrictMode cleanup must not display cancellation errors')
  }
  assert.deepEqual(errors,[])
  await page.close()
  console.log('UI audit: permission suppresses stale model dock; StrictMode marketplace cancellation; bilingual responsive rendering passed')
},{entry:'/__audit-entry.js',vite:{plugins:[{name:'audit-fixture',resolveId(id){if(id==='/__audit-entry.js')return id},load(id){if(id==='/__audit-entry.js')return entry},configureServer(server){server.middlewares.use('/__audit',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__audit',html))}catch(error){next(error)}})}}]}})
