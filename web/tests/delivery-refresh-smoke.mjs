import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TurnCompletionBar} from '/src/components/chat/blocks/TurnCompletionBar.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.copy=t;window.locale=setLocale
const root=createRoot(document.getElementById('root'))
window.show=owner=>{
 useAppStore.setState({activeSessionId:owner,sessions:[]})
 root.render(React.createElement(TurnCompletionBar,{sessionId:owner,row:{kind:'completion',id:'complete',turn:1,endSeq:10,finalSeq:9,at:1}}))
}
window.show('owner')
`

function completion(status) {
  return {turn:1,end_seq:10,final_response_seq:9,response_seq:9,ended_at:1,feedback:null,
    delivery:{run_id:'original-run',start_seq:0,tool_executions:1,failed_tools:0,unknown_tools:0,
      artifacts:[],artifact_count:0,verification:status,
      checks:status==='unverified'?[]:[{source_seq:8,result_seq:7,check_id:'Original check 原文',paths:['original.py'],status}]},
    hooks:{total:0,runs:0,blocked:0,unsuccessful:0,counts:{},duration_ms:0,rows:[],offset:0,next_offset:null}}
}

await browserScenario(async({server,browser})=>{
  const page=await browser.newPage(),errors=[],requests=[],aborted=new Set()
  page.on('pageerror',error=>errors.push(error.message))
  page.on('requestfailed',request=>aborted.add(request.url()))
  let phase='passed',pending,enter
  await page.route('**/api/v1/**',async route=>{
    const request=route.request(),url=request.url();requests.push(request.method())
    assert.ok(new URL(url).pathname.endsWith('/completion'))
    if(url.includes('/other/'))return route.fulfill({json:completion('unverified')})
    if(phase==='pending'){
      await new Promise(resolve=>{pending={route,resolve,url};enter()})
      return
    }
    return route.fulfill({json:completion(phase)})
  })
  const copy=key=>page.evaluate(key=>window.copy(key),key)
  await page.goto(server.resolvedUrls.local[0]+'__delivery-refresh')
  const results=page.locator('[data-turn-results="1"]')
  await results.locator('summary').click()
  await results.locator('[data-verification-state="passed"]').waitFor()
  const startPending=async()=>{
    phase='pending';const entered=new Promise(resolve=>enter=resolve)
    await results.getByRole('button',{name:await copy('ui.delivery.recheck'),exact:true}).click()
    await entered
    await results.locator('[data-verification-state="checking"]').waitFor()
  }
  const release=async response=>{
    const held=pending
    try{await held.route.fulfill(response)}catch(error){assert.ok(aborted.has(held.url),String(error))}
    finally{held.resolve()}
  }
  await startPending()
  for(const locale of ['en','zh-CN','en'])for(const width of [390,1440])for(const size of [10,22])for(const dark of [false,true]){
    await page.setViewportSize({width,height:900})
    await page.evaluate(({locale,size,dark})=>{window.locale(locale);document.documentElement.style.fontSize=size+'px';document.documentElement.classList.toggle('dark',dark)},{locale,size,dark})
    await results.getByText(await copy('ui.delivery.checking'),{exact:true}).waitFor()
    assert.equal(await results.getByText('Original check 原文',{exact:false}).isVisible(),false)
  }
  assert.equal(requests.length,2)
  await release({status:503,json:{code:'fixture/unavailable',error:'Original failure',copy:{key:'ui.http.invalidResponse'}}})
  await page.waitForFunction(()=>document.querySelector('[data-turn-completion]')?.getAttribute('aria-busy')==='false')
  await results.locator('[data-verification-state="unknown"]').waitFor()
  await results.getByText(await copy('ui.delivery.readUnconfirmed'),{exact:true}).waitFor()
  const history=results.locator('details')
  await history.locator('summary').click()
  await history.getByText('Original check 原文',{exact:false}).waitFor()
  await page.evaluate(()=>window.locale('zh-CN'))
  assert.notEqual(await history.getAttribute('open'),null)
  assert.equal(requests.length,2)
  phase='stale'
  await results.getByRole('button',{name:await copy('ui.delivery.recheck'),exact:true}).click()
  await results.locator('[data-verification-state="stale"]').waitFor()
  assert.equal(requests.length,3)
  await startPending()
  await page.evaluate(()=>window.show('other'))
  await page.waitForFunction(()=>document.querySelector('[data-turn-completion]')?.getAttribute('aria-busy')==='false')
  await results.locator('summary').click()
  await release({json:completion('passed')})
  await results.locator('[data-verification-state="unverified"]').waitFor()
  assert.equal(await results.getByText('Original check 原文',{exact:false}).count(),0)
  assert.ok(requests.every(method=>method==='GET'))
  assert.deepEqual(errors,[])
  await page.close()
  console.log('Delivery refresh: pending/failed reads keep historical evidence distinct, recovery replaces status, locale preserves reads, and late owner responses cannot claim a pass')
},{entry:'/__delivery-refresh-entry.js',vite:{plugins:[{name:'delivery-refresh',resolveId(id){if(id==='/__delivery-refresh-entry.js')return id},load(id){if(id==='/__delivery-refresh-entry.js')return entry},configureServer(server){server.middlewares.use('/__delivery-refresh',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__delivery-refresh','<html><body><div id="root"></div><script type="module" src="/__delivery-refresh-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
