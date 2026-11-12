import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {McpServicePanel} from '/src/components/overlays/McpServicePanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.copy=t;window.locale=setLocale
createRoot(document.getElementById('root')).render(React.createElement(McpServicePanel))
`
await browserScenario(async({server,browser})=>{
 for(const own of [true,false,'combined','stopped','starting','running','stopping']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));let reads=0
  const failed=typeof own==='boolean'||own==='combined',status=failed?'failed':own,writes=[]
  const addresses={local_url:'http://127.0.0.1/Original/{local}',public_url:'https://fixture.invalid/原文/{public}'}
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  await page.route('**/api/v1/**',async route=>{
   if(route.request().method()!=='GET')writes.push(route.request().method()+' '+new URL(route.request().url()).pathname)
   assert.equal(new URL(route.request().url()).pathname,'/api/v1/mcp-service')
   reads++;return route.fulfill({json:{status,...addresses,project_root:'',error:failed?'Original backend diagnostic 原文':'',...(own==='combined'?{error_details:[{key:'ui.mcpService.error.architecture',params:{machine:'OriginalMachine'}},'Original cleanup diagnostic 原文']}:{}),error_copy:failed&&own?{key:'ui.mcpService.error.architecture',params:{machine:'OriginalMachine'}}:null}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__mcp-state-language')
  await page.getByText(await page.evaluate(status=>window.copy('ui.mcpService.'+status),status),{exact:true}).waitFor()
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
   await page.getByText(await page.evaluate(status=>window.copy('ui.mcpService.'+status),status),{exact:true}).waitFor()
   if(failed){
    const expected=own==='combined'?await page.evaluate(()=>window.copy('ui.common.feedbackPair',{first:window.copy('ui.mcpService.error.architecture',{machine:'OriginalMachine'}),second:'Original cleanup diagnostic 原文'})):own?await page.evaluate(()=>window.copy('ui.mcpService.error.architecture',{machine:'OriginalMachine'})):'Original backend diagnostic 原文'
    await page.getByText(expected,{exact:true}).waitFor()
   }else assert.equal(await page.getByRole('alert').count(),0)
   if(status==='running')for(const [name,value] of [['local',addresses.local_url],['public',addresses.public_url]]){
    const label=await page.evaluate(name=>window.copy('ui.mcpService.'+name),name)
    assert.equal(await page.getByRole('textbox',{name:label,exact:true}).inputValue(),value)
   }
   assert.equal(reads,1);assert.deepEqual(writes,[])
  }
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('MCP five states, local/public labels and error resources change language without polling or writes; original addresses and diagnostics stay untouched')
},{entry:'/__mcp-state-language-entry.js',vite:{plugins:[{
 name:'mcp-state-language-fixture',resolveId(id){if(id==='/__mcp-state-language-entry.js')return id},load(id){if(id==='/__mcp-state-language-entry.js')return entry},configureServer(server){server.middlewares.use('/__mcp-state-language',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__mcp-state-language','<html><body><div id="root"></div><script type="module" src="/__mcp-state-language-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
