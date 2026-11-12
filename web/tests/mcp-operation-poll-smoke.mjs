import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {McpServicePanel} from '/src/components/overlays/McpServicePanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[]
const state=status=>({status,local_url:'http://localhost/mcp',public_url:'',project_root:'',workspaces:[],error:'',error_copy:null})
const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}})
window.fetch=async(path,options={})=>{
 const method=options.method||'GET';window.calls.push({method,path})
 if(method==='POST')return new Promise(resolve=>{window.finishStart=()=>resolve(response(state('running')))})
 if(window.calls.length===1)return response(state('stopped'))
 return new Promise(resolve=>{window.finishPoll=()=>resolve(response(state('stopped')))})
}
createRoot(document.getElementById('root')).render(React.createElement(McpServicePanel))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install();await page.goto(server.resolvedUrls.local[0]+'__mcp-operation')
 assert.equal(await page.getByRole('alert').count(),0)
 await page.getByRole('button',{name:'Start MCP',exact:true}).click()
 await page.waitForFunction(()=>typeof window.finishStart==='function')
 await page.clock.runFor(2100)
 const readsDuringWrite=await page.evaluate(()=>window.calls.filter(call=>call.method==='GET').length)
 await page.evaluate(()=>window.finishStart())
 await page.getByRole('button',{name:'Stop MCP',exact:true}).waitFor()
 if(await page.evaluate(()=>typeof window.finishPoll==='function')){
  await page.evaluate(()=>window.finishPoll());await page.clock.runFor(32)
 }
 assert.equal(await page.getByRole('button',{name:'Stop MCP',exact:true}).count(),1,'Poll started during an accepted write must not overwrite its result')
 assert.equal(readsDuringWrite,1,'Status observation pauses during accepted start')
 assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.method==='POST').length),1)
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const stop=await page.evaluate(()=>window.copy('ui.mcpService.stop'))
  await page.getByRole('button',{name:stop,exact:true}).waitFor()
  await page.getByText(locale==='en'?'Running':'运行中',{exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.method==='POST').length),1)
 }
 assert.deepEqual(errors,[]);await page.close()
 console.log('MCP operation: no overlapping status read or duplicate start; latest start result and bilingual UI retained')
},{entry:'/__mcp-operation-entry.js',vite:{plugins:[{name:'mcp-operation-fixture',resolveId(id){if(id==='/__mcp-operation-entry.js')return id},load(id){if(id==='/__mcp-operation-entry.js')return entry},configureServer(server){server.middlewares.use('/__mcp-operation',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__mcp-operation','<html><body><div id="root"></div><script type="module" src="/__mcp-operation-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
