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
 const page=await browser.newPage(),errors=[],writes=[]
 page.on('pageerror',error=>errors.push(error.message))
 let rejectNamed=false
 let state={status:'stopped',local_url:'',public_url:'',project_root:'',error:'',error_copy:null,tunnel:{mode:'quick',public_url:'',origin_port:18765,has_token:false,token_mask:''},quick_retry_at:0,reconnecting:false}
 await page.clock.install()
 await page.route('**/api/v1/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(request.method()==='POST'){
   writes.push({path,body:request.postDataJSON()})
   if(path.endsWith('/start')&&rejectNamed)return route.fulfill({status:400,json:{code:'mcp/tunnel-config',error:'Original server diagnostic',details:{copy:{key:'ui.mcpService.error.tunnelConfig',params:{}}}}})
   if(path.endsWith('/start'))state={...state,status:'starting',tunnel:{mode:'named',public_url:'https://mcp.example.test',origin_port:18766,has_token:true,token_mask:'******oken'}}
   else if(path.endsWith('/stop'))state={...state,status:'stopped',reconnecting:false}
  }
  return route.fulfill({json:state})
 })
 await page.goto(server.resolvedUrls.local[0]+'__mcp-named')
 const label=key=>page.evaluate(key=>window.copy('ui.mcpService.'+key),key)
 await page.getByRole('button',{name:'Start MCP',exact:true}).waitFor()
 await page.getByLabel(await label('tunnelMode'),{exact:true}).selectOption('named')
 await page.getByLabel(await label('hostname'),{exact:true}).fill('https://mcp.example.test')
 await page.getByLabel(await label('originPort'),{exact:true}).fill('18766')
 await page.getByLabel(await label('tunnelToken'),{exact:true}).fill('private-fixture-token')
 await page.clock.runFor(2100)
 assert.equal(await page.getByLabel(await label('tunnelMode'),{exact:true}).inputValue(),'named','Polling must not erase edited configuration')
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:1000});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
  const token=page.getByLabel(await label('tunnelToken'),{exact:true})
  assert.equal(await token.getAttribute('type'),'password')
  assert.equal(await token.inputValue(),'private-fixture-token')
  await token.focus();assert.equal(await token.evaluate(element=>element===document.activeElement),true)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  assert.equal(writes.length,0)
 }
 await page.getByRole('button',{name:'Start MCP',exact:true}).click()
 await page.getByRole('button',{name:'Stop MCP',exact:true}).waitFor()
 assert.equal(await page.getByRole('button',{name:'Stop MCP',exact:true}).isEnabled(),true,'Accepted startup can be stopped')
 for(const locale of ['en','zh-CN','en']) {
  await page.evaluate(locale=>window.locale(locale),locale)
  await page.getByText(await label('startingNamed'),{exact:true}).waitFor()
  assert.equal(await page.getByText(await label('starting'),{exact:true}).count(),0)
 }
 assert.deepEqual(writes[0].body,{scope:'all',tunnel:{mode:'named',public_url:'https://mcp.example.test',origin_port:18766,token:'private-fixture-token'}})
 assert.equal(await page.getByLabel(await label('tunnelToken'),{exact:true}).inputValue(),'')
 assert.equal(await page.getByLabel(await label('tunnelToken'),{exact:true}).getAttribute('placeholder'),'******oken')
 state={...state,status:'running',password_mask:'******Ab34',public_url:'https://mcp.example.test/mcp',local_url:'http://127.0.0.1:12345/mcp',reconnecting:true}
 await page.clock.runFor(2100)
 await page.getByText(await label('reconnecting'),{exact:true}).waitFor()
 for(const locale of ['en','zh-CN','en']) {
  await page.evaluate(locale=>window.locale(locale),locale)
  assert.equal(await page.getByLabel(await label('password'),{exact:true}).inputValue(),'******Ab34')
  await page.getByLabel(await label('publicNamed'),{exact:true}).waitFor()
  assert.equal(await page.getByLabel(await label('tunnelToken'),{exact:true}).getAttribute('placeholder'),'******oken')
  assert.equal(await page.getByText(await label('tokenSaved'),{exact:true}).count(),0)
 }
 assert.equal(writes.length,1,'Reconnecting must not launch another tunnel')
 await page.getByRole('button',{name:'Stop MCP',exact:true}).click()
 await page.getByRole('button',{name:'Start MCP',exact:true}).waitFor()
 state={...state,status:'failed',tunnel:{...state.tunnel,mode:'quick'},quick_retry_at:Math.floor(Date.now()/1000)+3600,error:'rate limit',error_copy:{key:'ui.mcpService.error.rateLimited'}}
 await page.clock.runFor(2100)
 await page.getByText(await label('cooldown'),{exact:true}).waitFor()
 assert.equal(await page.getByRole('button',{name:'Start MCP',exact:true}).isDisabled(),true)
 await page.getByLabel(await label('tunnelMode'),{exact:true}).selectOption('named')
 assert.equal(await page.getByRole('button',{name:'Start MCP',exact:true}).isEnabled(),true,'Named tunnels remain available during temporary provisioning cooldown')
 assert.equal(writes.length,2)
 rejectNamed=true
 await page.getByRole('button',{name:'Start MCP',exact:true}).click()
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  await page.getByRole('alert').filter({hasText:await label('error.tunnelConfig')}).waitFor()
 }
 await page.clock.runFor(2100)
 await page.getByRole('alert').filter({hasText:await label('error.tunnelConfig')}).waitFor()
 assert.equal(writes.length,3,'Validation errors and language changes do not replay accepted writes')
 assert.deepEqual(errors,[]);await page.close()
 console.log('Named tunnel settings: bilingual/theme/width matrix, protected token, draft isolation, startup stop, reconnect and rate-limit controls passed')
},{entry:'/__mcp-named-entry.js',vite:{plugins:[{name:'mcp-named-fixture',resolveId(id){if(id==='/__mcp-named-entry.js')return id},load(id){if(id==='/__mcp-named-entry.js')return entry},configureServer(server){server.middlewares.use('/__mcp-named',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__mcp-named','<html><body><div id="root"></div><script type="module" src="/__mcp-named-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
