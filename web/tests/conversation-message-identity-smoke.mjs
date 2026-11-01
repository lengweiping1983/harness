import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ConversationSearch} from '/src/components/overlays/ConversationSearch.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',title:'Original conversation',cwd:'/fixture'}],conversationSearchOpen:true})
createRoot(document.getElementById('root')).render(React.createElement(ConversationSearch))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['session','missing-session','seq','role','start','length','next','previous','valid']){
  const page=await browser.newPage(),errors=[];let searches=0,reads=0
  page.on('pageerror',error=>errors.push(error.message))
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  await page.route('**/api/v1/**',route=>{
   const request=route.request();assert.equal(request.method(),'GET')
   if(new URL(request.url()).pathname.endsWith('/search')){searches++;return route.fulfill({json:{session_id:'fixture',query:'needle',through:9,total:1,results:[{source_seq:7,source_time:1,role:'assistant',prefix:'',match:'needle',suffix:'',start_char:0}],has_more:false,next_before:null}})}
   reads++;assert.ok(request.url().includes('/messages/7?start=0'))
   return route.fulfill({json:{session_id:mode==='session'?'other':mode==='missing-session'?undefined:'fixture',source_seq:mode==='seq'?8:7,source_time:1,role:mode==='role'?'user':'assistant',text:'needle😀',start:mode==='start'?1:0,end:mode==='length'?8:7,total_chars:mode==='length'?8:7,previous_start:mode==='previous'?0:null,next_start:mode==='next'?0:null}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__message-identity')
  await page.locator('[data-conversation-search]').fill('needle');await page.clock.runFor(300)
  await page.locator('li button').first().click()
  const label=key=>page.evaluate(key=>window.copy(key),key)
  if(mode==='valid')await page.getByText('needle😀',{exact:true}).waitFor()
  else await page.getByRole('alert').filter({hasText:await label('ui.http.invalidResponse')}).waitFor()
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   if(mode!=='valid'){await page.getByRole('alert').filter({hasText:await label('ui.http.invalidResponse')}).waitFor();assert.equal(await page.getByText('needle😀',{exact:true}).count(),0)}
   else await page.getByText('needle😀',{exact:true}).waitFor()
   assert.equal(await page.getByRole('button',{name:await label('ui.components.overlays.ConversationSearch.b14e690de35a'),exact:true}).count(),mode==='valid'?1:0)
   assert.equal(searches,1);assert.equal(reads,1)
  }
  assert.deepEqual(errors,[]);await page.close();console.log('Conversation original identity and Unicode range, without extra requests: '+mode)
 }
},{entry:'/__message-identity-entry.js',vite:{plugins:[{name:'message-identity-fixture',resolveId(id){if(id==='/__message-identity-entry.js')return id},load(id){if(id==='/__message-identity-entry.js')return entry},configureServer(server){server.middlewares.use('/__message-identity',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__message-identity','<html><body><div id="root"></div><script type="module" src="/__message-identity-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
