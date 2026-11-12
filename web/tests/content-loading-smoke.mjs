import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
import {withDeadline} from './fixture-cleanup.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Markdown} from '/src/components/markdown/Markdown.tsx'
import {Mermaid} from '/src/components/markdown/Mermaid.tsx'
import {TerminalCanvas} from '/src/components/inspector/TerminalWorkbench.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en')
const terminal={sessionId:'terminal',transport:'pty',cwd:'/fixture',status:{kind:'running'}}
api.terminalStream=async()=>({ok:true,data:{terminal,text:'',cursor:0,end:0,eof:false,reset:false}})
api.resizeTerminal=async()=>({ok:true,data:{}})
api.terminalInput=async()=>{throw Error('Unexpected terminal write')}
const root=createRoot(document.getElementById('root'))
window.show=mode=>root.render(mode==='math'?React.createElement(Markdown,null,'$x+y$'):mode==='mermaid'?React.createElement(Mermaid,{chart:'graph TD; A-->B'}):React.createElement(TerminalCanvas,{sessionId:'fixture',terminal,active:true,onUpdate:()=>{}}))
`
await browserScenario(async({server,browser})=>{
 for(const [mode,path,fragment] of [['app','/','/src/App.tsx'],['shared','/shared#token=fixture','/src/components/shared/SharedSessionPage.tsx'],['invitation','/shared#token=hi1.fixture.secret','/src/components/shared/InvitationPage.tsx']]){
  const page=await browser.newPage(),errors=[];let pending,entered
  const requested=new Promise(resolve=>{entered=resolve})
  page.on('pageerror',e=>errors.push(e.message));await page.clock.install()
  await page.route('**/*',route=>{if(route.request().url().includes(fragment)){pending=route;entered();return}return route.continue()})
  await page.goto(server.resolvedUrls.local[0].replace(/\/$/,'')+path)
  await withDeadline(() => requested, 30000, mode+' module request')
  await page.getByRole('status').waitFor();await page.clock.runFor(10001);await page.getByRole('alert').waitFor()
  assert.ok(pending);assert.equal(await page.getByRole('status').count(),0)
  await pending.abort('failed');assert.deepEqual(errors,[]);await page.close();console.log(mode+': startup module timeout is recoverable')
 }
 for(const mode of ['math','mermaid','terminal']){
  const page=await browser.newPage(),errors=[];let pending,entered
  const requested=new Promise(resolve=>{entered=resolve})
  page.on('pageerror',e=>errors.push(e.message));await page.clock.install()
  await page.route('**/*',route=>{
   const url=route.request().url(),match=mode==='math'?url.includes('/rehype-katex'):mode==='mermaid'?/\/mermaid(?:\.js|\/)/.test(url):url.includes('@xterm_xterm')
   if(match){pending=route;entered();return}return route.continue()
  })
  await page.goto(server.resolvedUrls.local[0]+'__content-loading');await page.waitForFunction(()=>typeof window.show==='function')
  await page.evaluate(mode=>window.show(mode),mode)
  await withDeadline(() => requested, 30000, mode+' module request')
  await page.clock.runFor(10001)
  if(mode==='mermaid')await page.locator('pre code').filter({hasText:'graph TD; A-->B'}).waitFor()
  else await page.getByRole('alert').waitFor()
  assert.ok(pending,mode+' module request must be held')
  await pending.continue()
  if(mode==='math')await page.locator('.katex').first().waitFor()
  if(mode==='mermaid')await page.locator('svg[id^="mmd-"]').waitFor()
  if(mode==='terminal')await page.locator('.xterm-helper-textarea').waitFor({state:'attached'})
  assert.equal(await page.getByRole('alert').count(),0)
  assert.deepEqual(errors,[]);await page.close();console.log(mode+': pending module timeout preserves readable content and late success recovers')
 }
},{entry:'/__content-loading-entry.js',vite:{plugins:[{
 name:'content-loading-fixture',enforce:'pre',resolveId(id){if(id==='/__content-loading-entry.js')return id},async load(id){if(id.endsWith('/TerminalWorkbench.tsx'))return await readFile(id,'utf8')+'\nexport {TerminalCanvas}\n';if(id==='/__content-loading-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__content-loading',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__content-loading','<html><body><div id="root" style="height:800px"></div><script type="module" src="/__content-loading-entry.js"></script></body></html>'))}catch(e){next(e)}})}
}]}})
