import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TerminalCanvas} from '/src/components/inspector/TerminalWorkbench.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;window.reads=0;window.writes=0;setLocale('en')
let terminal={sessionId:'terminal',transport:'pty',cwd:'/fixture',status:{kind:'running'}}
api.terminalStream=async()=>{window.reads++;return {ok:true,data:{terminal,text:'',cursor:0,end:0,eof:false,reset:false}}}
api.resizeTerminal=async()=>({ok:true,data:{}})
api.terminalInput=async(_owner,_id,text,newline)=>{window.writes++;window.lastInput={text,newline};return {ok:true,data:{}}}
const root=createRoot(document.getElementById('root'));const render=()=>root.render(React.createElement(TerminalCanvas,{sessionId:'owner',terminal,active:true,onUpdate:()=>{}}));window.pipe=()=>{terminal={...terminal,transport:'pipe'};render()};window.end=(exit_code,signal)=>{terminal={...terminal,status:{kind:'exited',exit_code,signal}};render()};render()
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 await page.goto(server.resolvedUrls.local[0]+'__terminal-input-label')
 await page.locator('.xterm-helper-textarea').waitFor({state:'attached'})
 await page.waitForFunction(()=>window.reads===1)
 await page.evaluate(()=>{window.original=document.querySelector('.xterm-helper-textarea')})
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
  const label=await page.evaluate(()=>window.copy('ui.components.inspector.TerminalWorkbench.7539edf17e87'))
  await page.getByRole('textbox',{name:label,exact:true}).waitFor({state:'attached'})
  assert.ok(await page.evaluate(()=>document.querySelector('.xterm-helper-textarea')===window.original),'locale must retain emulator instance')
  assert.equal(await page.evaluate(()=>window.reads),1)
  assert.equal(await page.evaluate(()=>window.writes),0)
 }
 await page.evaluate(()=>window.pipe())
 await page.waitForFunction(()=>window.reads===2)
 for(const locale of ['zh-CN','en'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(locale=>window.locale(locale),locale)
  const commandLabel=await page.evaluate(()=>window.copy('ui.components.inspector.TerminalWorkbench.a065d91d9713'))
  const command=page.getByRole('textbox',{name:commandLabel,exact:true});await command.waitFor()
  assert.equal(await page.getByRole('textbox').count(),1,'Command mode exposes only one input')
  assert.equal(await command.isEnabled(),true)
  assert.equal(await page.locator('.xterm-helper-textarea').getAttribute('aria-hidden'),'true')
  assert.equal(await page.locator('.xterm-helper-textarea').getAttribute('tabindex'),'-1')
  await page.getByRole('log',{name:await page.evaluate(()=>window.copy('ui.terminal.output'))}).waitFor()
  assert.equal(await page.locator('footer').textContent(),await page.evaluate(()=>window.copy('ui.components.inspector.TerminalWorkbench.804dda88359b')))
 }
 await page.getByRole('textbox').fill('echo fixture')
 await page.locator('form button[type="submit"]').click()
 await page.waitForFunction(()=>window.writes===1)
 assert.deepEqual(await page.evaluate(()=>window.lastInput),{text:'echo fixture',newline:true})
 assert.equal(await page.getByRole('textbox').inputValue(),'')
 for(const [code,signal,key,params] of [[0,null,'ui.terminal.exitedCode',{code:0}],[null,'TERM','ui.terminal.exitedSignal',{signal:'TERM'}]]){
  await page.evaluate(({code,signal})=>window.end(code,signal),{code,signal})
  for(const locale of ['en','zh-CN','en']){
   await page.evaluate(locale=>window.locale(locale),locale)
   const label=await page.evaluate(({key,params})=>window.copy(key,params),{key,params})
   await page.getByText(label,{exact:true}).waitFor()
   assert.ok(!label.includes('·'))
  }
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Xterm input label follows locale without recreating emulator, polling or sending input')
},{entry:'/__terminal-input-label-entry.js',vite:{plugins:[{
 name:'terminal-input-label-fixture',enforce:'pre',resolveId(id){if(id==='/__terminal-input-label-entry.js')return id},async load(id){if(id.endsWith('/TerminalWorkbench.tsx'))return await readFile(id,'utf8')+'\nexport {TerminalCanvas}\n';if(id==='/__terminal-input-label-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__terminal-input-label',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__terminal-input-label','<html><body><div id="root" style="height:800px"></div><script type="module" src="/__terminal-input-label-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
