import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {WorktreeEnvironment} from '/src/components/overlays/WorktreeEnvironment.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(WorktreeEnvironment,{id:'worktree',sessionId:'fixture',defaultOpen:true}))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['wrong-worktree','wrong-fingerprint','preparing','ready','failed']){
  const page=await browser.newPage();const errors=[];let writes=0,reads=0
  page.on('pageerror',error=>errors.push(error.message))
  const fingerprint='a'.repeat(64), confirmed=['ready','failed'].includes(mode)
  const receipt={receipt_id:'receipt',worktree_id:mode==='wrong-worktree'?'foreign':'worktree',fingerprint:mode==='wrong-fingerprint'?'b'.repeat(64):fingerprint,status:mode==='preparing'?'preparing':mode==='failed'?'failed':'ready',planned_count:1,copied_count:mode==='ready'?1:0,skipped_count:0,reused:false,truncated:false,entries:[]}
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),url=new URL(request.url())
   if(request.method()==='POST'){writes++;assert.deepEqual(request.postDataJSON(),{expected_fingerprint:fingerprint});return route.fulfill({json:{receipt}})}
   reads++
   return route.fulfill({json:url.pathname.endsWith('/receipt')?{receipt:writes?receipt:null}:{configured:true,fingerprint,copy_count:1,skip_count:0,total_bytes:1,offset:0,next_offset:null,entries:[{path:'.env',action:'copy',size:1}]}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__environment-identity')
  const label=(key,params)=>page.evaluate(({key,params})=>window.copy(key,params),{key,params})
  await page.getByRole('button',{name:await label('ui.environment.import',{count:1}),exact:true}).click()
  if(confirmed)await page.getByText(await label('ui.environment.status.'+mode),{exact:true}).waitFor()
  else await page.getByRole('alert').filter({hasText:await label('ui.environment.unconfirmed')}).waitFor()
  if(!confirmed)for(const locale of ['zh-CN','en']){
   await page.evaluate(locale=>window.locale(locale),locale)
   await page.getByRole('alert').filter({hasText:await label('ui.environment.unconfirmed')}).waitFor()
   assert.equal(writes,1)
  }
  const pending=()=>page.evaluate(()=>localStorage.getItem('harness.environment.pending.fixture.worktree'))
  assert.equal(await pending(),confirmed?null:fingerprint)
  await page.getByRole('button',{name:await label(confirmed?'ui.environment.refresh':'ui.environment.check'),exact:true}).click()
  await page.getByRole('button',{name:await label('ui.environment.import',{count:1}),exact:true}).waitFor()
  if(mode==='wrong-worktree'){
   await page.getByRole('alert').filter({hasText:await label('ui.environment.unconfirmed')}).waitFor()
   assert.equal(await page.getByText(await label('ui.environment.status.ready'),{exact:true}).count(),0,'Foreign worktree receipt must not be presented as local completion')
  }
  const before=reads
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   const button=page.getByRole('button',{name:await label('ui.environment.import',{count:1}),exact:true})
   assert.equal(await button.isDisabled(),!confirmed)
   assert.equal(await pending(),confirmed?null:fingerprint)
   if(!confirmed)await page.getByText(await label('ui.environment.unknown'),{exact:true}).waitFor()
   assert.equal(writes,1);assert.equal(reads,before)
  }
  assert.deepEqual(errors,[]);await page.close();console.log('Environment receipt identity, pending state, and one write: '+mode)
 }
},{entry:'/__environment-identity-entry.js',vite:{plugins:[{name:'environment-identity-fixture',resolveId(id){if(id==='/__environment-identity-entry.js')return id},load(id){if(id==='/__environment-identity-entry.js')return entry},configureServer(server){server.middlewares.use('/__environment-identity',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__environment-identity','<html><body><div id="root"></div><script type="module" src="/__environment-identity-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
