import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {SharedSessionPage} from '/src/components/shared/SharedSessionPage.tsx'
import {McpServicePanel} from '/src/components/overlays/McpServicePanel.tsx'
import {ConfirmRemoval} from '/src/components/ui/ConfirmRemoval.tsx'
import {setLocale,t,useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;setLocale('en');window.removals=0
const view=new URLSearchParams(location.search).get('view')
function Remove(){useLocale();return React.createElement(ConfirmRemoval,{name:'Original-{name} 原文',prompt:t('ui.catalog.removeConnectorConfirm',{name:'Original-{name} 原文'}),disabled:false,onConfirm:async()=>{window.removals++;return true}})}
createRoot(document.getElementById('root')).render(view==='shared'?React.createElement(SharedSessionPage,{initialToken:''}):view==='mcp'?React.createElement(McpServicePanel):React.createElement(Remove))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['shared','mcp','remove']){
  const page=await browser.newPage();page.setDefaultTimeout(5000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   requests.push(route.request().method())
   return route.fulfill({json:view==='shared'?{enabled:true,authenticated:false,subject:null,session_ids:[],credential_id:null,read_only:true,capabilities:{}}:{status:'failed',local_url:'',public_url:'',project_root:'',workspaces:[],error:'External original',error_copy:{key:'ui.mcpService.error.startTimeout'}}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__access-guidance?view='+view)
  if(view==='shared')await page.locator('input[type=password]').fill('Original credential-{name}')
  if(view==='remove')await page.getByRole('button',{name:'Remove “Original-{name} 原文”',exact:true}).click()
  if(view==='mcp')await page.getByText('MCP startup timed out. Check its status before trying again.',{exact:true}).waitFor()
  const count=requests.length
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   const copy=view==='shared'?(locale==='en'?'Paste the access credential provided by the inviter. Your assigned role determines whether you can read the conversation or submit tasks.':'粘贴邀请者提供的访问凭据。是否可阅读对话或提交任务，由分配给你的角色决定。'):view==='mcp'?(locale==='en'?'MCP startup timed out. Check its status before trying again.':'MCP 启动超时，请先核实状态后再决定是否重试。'):(locale==='en'?'Remove MCP service “Original-{name} 原文”? You will need to add it again to use it.':'删除 MCP“Original-{name} 原文”？需重新添加后才能继续使用。')
   await page.getByText(copy,{exact:true}).waitFor()
   if(view==='shared')assert.equal(await page.locator('input[type=password]').inputValue(),'Original credential-{name}')
   assert.equal(requests.length,count);assert.equal(await page.evaluate(()=>window.removals),0)
  }
  if(view==='remove'){
   await page.getByRole('button',{name:'Confirm deletion',exact:true}).click()
   await page.getByRole('button',{name:'Remove “Original-{name} 原文”',exact:true}).waitFor()
   assert.equal(await page.evaluate(()=>window.removals),1)
  }
  assert.ok(requests.every(method=>method==='GET'));assert.deepEqual(errors,[])
  await page.close();console.log(view+': accurate bilingual guidance, original identity and no repeated writes passed')
 }
},{entry:'/__access-guidance-entry.js',vite:{plugins:[{name:'access-guidance-fixture',resolveId(id){if(id==='/__access-guidance-entry.js')return id},load(id){if(id==='/__access-guidance-entry.js')return entry},configureServer(server){server.middlewares.use('/__access-guidance',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__access-guidance','<html><body><div id="root"></div><script type="module" src="/__access-guidance-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
