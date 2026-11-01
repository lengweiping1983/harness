import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {FileRestore} from '/src/components/inspector/FileRestore.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
const target={sessionId:'fixture',turn:1},file={path:'/fixture/a.txt',first_seq:2,last_seq:3,operations:1,available:true,kind:'modified',counts:null,truncated:false,reason:null}
createRoot(document.getElementById('root')).render(React.createElement(FileRestore,{target,file,through:4}))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['lost-response','unknown-receipt','saved-unknown','restored']){
  const page=await browser.newPage(),errors=[],requests=[];let requestId='saved-request',resolved=false
  page.on('pageerror',error=>errors.push(error.message))
  const receipt=()=>({session_id:'fixture',turn:1,source_seq:2,request_id:requestId,path:'/fixture/a.txt',action:'restore',status:resolved||mode==='restored'?'restored':'unknown',message:'Original receipt 原文',time:1})
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),url=new URL(request.url());requests.push({method:request.method(),path:url.pathname})
   if(request.method()==='POST'){
    requestId=request.postDataJSON().request_id
    return mode==='lost-response'?route.fulfill({status:500,json:{error:{code:'fixture/unconfirmed',message:'Original transport 原文'}}}):route.fulfill({json:receipt()})
   }
   if(url.pathname.includes('/restores/'))return mode==='lost-response'&&!resolved?route.fulfill({status:404,json:{error:{code:'fixture/missing',message:'Original missing 原文'}}}):route.fulfill({json:receipt()})
   return route.fulfill({json:{session_id:'fixture',turn:1,source_seq:2,through:4,path:'/fixture/a.txt',available:true,reason:null,action:'restore',patch:'',truncated:false,expected_version:'a'.repeat(64),latest_receipt:mode==='saved-unknown'?receipt():null}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__restore-pending')
  const button=async key=>page.getByRole('button',{name:await page.evaluate(key=>window.copy('ui.components.inspector.FileRestore.'+key),key),exact:true})
  await (await button('a7c1cf1bb949')).click()
  if(mode!=='saved-unknown')await (await button('4273639151bb')).click()
  const expectedWrites=mode==='saved-unknown'?0:1
  if(mode==='restored')await page.getByText('Original receipt 原文',{exact:false}).waitFor()
  else await (await button('b1edad815670')).waitFor()
  await (await button('6edab17576b4')).click()
  if(mode==='restored')await (await button('4273639151bb')).waitFor()
  else await (await button('b1edad815670')).waitFor()
  await (await button('03fb52363419')).click();await (await button('a7c1cf1bb949')).click()
  if(mode==='restored')await (await button('4273639151bb')).waitFor()
  else await (await button('b1edad815670')).waitFor()
  const before=requests.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   assert.equal(await (await button('4273639151bb')).count(),mode==='restored'?1:0)
   if(mode!=='restored')await (await button('b1edad815670')).waitFor()
   await page.getByRole('heading',{name:locale==='en'?"Restore the file to its state before turn 1":'将文件恢复到第 1 轮次修改前',exact:true}).waitFor()
   assert.equal(requests.length,before);assert.equal(requests.filter(x=>x.method==='POST').length,expectedWrites)
  }
  if(mode!=='restored'){
   resolved=true;const response=page.waitForResponse(response=>response.url().includes('/restores/'))
   await (await button('b1edad815670')).click();await response
   await page.getByText('Original receipt 原文',{exact:false}).waitFor()
   assert.ok(requests.filter(x=>x.path.includes('/restores/')).every(x=>x.path.endsWith('/'+requestId)))
   assert.equal(requests.filter(x=>x.method==='POST').length,expectedWrites)
  }
  assert.deepEqual(errors,[]);await page.close();console.log('File restore refresh/reopen preserves pending request and read-only reconciliation: '+mode)
 }
},{entry:'/__restore-pending-entry.js',vite:{plugins:[{name:'restore-pending-fixture',resolveId(id){if(id==='/__restore-pending-entry.js')return id},load(id){if(id==='/__restore-pending-entry.js')return entry},configureServer(server){server.middlewares.use('/__restore-pending',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__restore-pending','<html><body><div id="root"></div><script type="module" src="/__restore-pending-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
