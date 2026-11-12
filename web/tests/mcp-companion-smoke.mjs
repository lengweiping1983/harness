import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import path from 'node:path'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {McpServicePanel} from '/src/components/overlays/McpServicePanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.copies=[]
Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.copies.push(text)}})
createRoot(document.getElementById('root')).render(React.createElement(McpServicePanel))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],writes=[],configurations=[]
 page.on('pageerror',error=>errors.push(error.message))
 let state={status:'running',local_url:'http://127.0.0.1:12345/mcp',public_url:'https://fixture.invalid/mcp',project_root:'',error:'',error_copy:null,
  companion:{enabled:true,port:8877,status:'running',endpoint:'http://127.0.0.1:8877/',executor:'ready',token_mask:'******aaaa',external:true,error:'',error_copy:null,extension_path:'D:\\work_video\\harness\\extensions\\chatgpt-companion',extension_version:'1.0.0'}}
 let holdToken=false,releaseToken,tokenHeld,holdConfigure=false,releaseConfigure,configureHeld
 await page.clock.install()
 await page.route('**/api/v1/mcp-service**',async route=>{
  const request=route.request(),url=new URL(request.url())
  if(request.method()==='GET')return route.fulfill({json:state})
  writes.push(url.pathname)
  if(url.pathname.endsWith('/companion/token')){
   if(holdToken){tokenHeld?.();await new Promise(resolve=>{releaseToken=resolve})}
   return route.fulfill({json:{token:'a'.repeat(64)}})
  }
  if(url.pathname.endsWith('/companion')){
   const value=request.postDataJSON();configurations.push(value)
   if(holdConfigure){configureHeld?.();await new Promise(resolve=>{releaseConfigure=resolve})}
   state={...state,companion:{...state.companion,...value,status:value.enabled?'running':'disabled',endpoint:'http://127.0.0.1:'+value.port+'/'}}
  }
  if(url.pathname.endsWith('/stop'))state={...state,status:'stopped',companion:{...state.companion,status:'stopped',executor:'offline'}}
  return route.fulfill({json:state})
 })
 await page.goto(server.resolvedUrls.local[0]+'__mcp-companion')
 const panel=page.locator('section section')
 const label=key=>page.evaluate(key=>window.copy('ui.companion.'+key),key)
 await panel.getByRole('heading',{name:'Conversation continuation',exact:true}).waitFor()
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:1100});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  await panel.getByRole('heading',{name:await label('title'),exact:true}).waitFor()
  assert.equal(await panel.getByLabel(await label('address'),{exact:true}).inputValue(),'http://127.0.0.1:8877/')
  assert.equal(await panel.getByLabel(await label('token'),{exact:true}).inputValue(),'******aaaa')
  assert.equal(await panel.getByRole('checkbox').count(),0)
  assert.equal(await panel.getByText(/Chrome.*Edge/).count(),1)
  assert.equal(await panel.getByText(/CMD|手工接续桥|manually started bridge/).count(),0)
  assert.equal(await panel.locator('input[readonly]').last().inputValue(),'D:\\work_video\\harness\\extensions\\chatgpt-companion')
  assert.equal(await panel.getByRole('button',{name:await label('disable'),exact:true}).count(),1)
  assert.equal(await panel.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.mcpService.stop')),exact:true}).count(),0)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  assert.equal(writes.length,0)
 }
 state={...state,companion:{...state.companion,status:'failed',error:'Original English error',error_copy:{key:'ui.companion.invalidConfig',params:{}}}}
 await page.clock.runFor(2100)
 await panel.getByRole('alert').waitFor()
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  assert.equal(await panel.getByRole('alert').textContent(),await label('invalidConfig'))
 }
 state={...state,companion:{...state.companion,status:'running',error:'',error_copy:null}}
 await page.clock.runFor(2100)
 await panel.getByRole('button',{name:'Show password or key',exact:true}).waitFor({state:'visible'})
 await panel.getByLabel(await label('port'),{exact:true}).fill('')
 holdConfigure=true
 const configurationAccepted=new Promise(resolve=>{configureHeld=resolve})
 await panel.getByRole('button',{name:'Disable continuation',exact:true}).evaluate(button=>{button.click();button.click()})
 await configurationAccepted
 assert.equal(configurations.length,1)
 assert.deepEqual(configurations[0],{enabled:false,port:8877})
 assert.equal(await page.getByRole('button',{name:'Stop MCP',exact:true}).isDisabled(),false)
 releaseConfigure();holdConfigure=false
 await panel.getByRole('button',{name:'Enable continuation',exact:true}).waitFor()
 assert.equal(await panel.getByLabel(await label('port'),{exact:true}).inputValue(),'')
 assert.equal(await page.getByRole('button',{name:'Stop MCP',exact:true}).count(),1)
 assert.equal(writes.includes('/api/v1/mcp-service/stop'),false)
 assert.equal(await panel.getByRole('button',{name:'Show password or key',exact:true}).isDisabled(),true)
 await panel.getByRole('button',{name:'Enable continuation',exact:true}).click()
 await panel.getByRole('button',{name:'Disable continuation',exact:true}).waitFor()
 assert.deepEqual(configurations[1],{enabled:true,port:8877})
 await panel.getByRole('button',{name:'Show password or key',exact:true}).click()
 await panel.getByRole('button',{name:'Hide password or key',exact:true}).waitFor()
 assert.equal(await panel.getByLabel(await label('token'),{exact:true}).inputValue(),'a'.repeat(64))
 await panel.getByRole('button',{name:'Hide password or key',exact:true}).click()
 await panel.getByLabel(await label('port'),{exact:true}).fill('8899')
 await page.clock.runFor(2100)
 assert.equal(await panel.getByLabel(await label('port'),{exact:true}).inputValue(),'8899')
 await panel.getByLabel(await label('port'),{exact:true}).blur()
 await page.waitForFunction(()=>document.querySelector('input[readonly]')!==null)
 await panel.getByLabel(await label('address'),{exact:true}).filter({visible:true}).waitFor()
 await page.waitForFunction(()=>[...document.querySelectorAll('input')].some(n=>n.value==='http://127.0.0.1:8899/'))
 holdToken=true
 const accepted=new Promise(resolve=>{tokenHeld=resolve})
 const reveal=panel.getByRole('button',{name:'Show password or key',exact:true}).click()
 await accepted;await reveal
 await page.getByRole('button',{name:'Stop MCP',exact:true}).click()
 await page.getByRole('button',{name:'Start MCP',exact:true}).waitFor()
 releaseToken()
 await panel.getByRole('button',{name:'Show password or key',exact:true}).waitFor()
 assert.notEqual(await panel.getByLabel(await label('token'),{exact:true}).inputValue(),'a'.repeat(64))
 assert.equal(writes.filter(value=>value.endsWith('/companion')).length,3)
 assert.deepEqual(errors,[])
 await page.evaluate(()=>window.locale('zh-CN'))
 await page.setViewportSize({width:1000,height:1400})
 await panel.getByRole('heading',{name:await label('title'),exact:true}).waitFor()
 await mkdir(path.resolve('../output'),{recursive:true})
 await page.screenshot({path:path.resolve('../output/mcp-companion-panel.png'),fullPage:true})
 console.log('MCP companion settings: separate button controls, bilingual themes and widths, actual fields, protected drafts, apply, lifecycle and late-token isolation passed')
},{entry:'/__mcp-companion-entry.js',vite:{plugins:[{name:'mcp-companion-fixture',resolveId(id){if(id==='/__mcp-companion-entry.js')return id},load(id){if(id==='/__mcp-companion-entry.js')return entry},configureServer(server){server.middlewares.use('/__mcp-companion',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__mcp-companion','<html><body><div id="root"></div><script type="module" src="/__mcp-companion-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
