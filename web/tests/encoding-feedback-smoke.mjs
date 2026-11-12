import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TerminalCanvas} from '/src/components/inspector/TerminalWorkbench.tsx'
import {ToolBlock} from '/src/components/chat/blocks/ToolBlock.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;window.reads=0;window.writes=0;setLocale('en')
const issue={channel:'stdout',encoding:'utf-8',offset:6,reason:'invalid start byte'}
const terminal={sessionId:'terminal',type:'fixture',pid:1,transport:'pty',cwd:'/fixture',status:{kind:'running'},encoding_errors:[issue]}
api.terminalStream=async()=>{window.reads++;return {ok:true,data:{terminal,text:'prefix',cursor:6,end:6,eof:false,reset:false}}}
api.resizeTerminal=async()=>({ok:true,data:{}})
api.terminalInput=async()=>{window.writes++;throw Error('Unexpected input')}
const block={id:'call',callId:'call',kind:'tool',at:1,name:'run_code',status:'running',input:{code:'original'},progress:{seq:1,stdout:'prefix',stderr:'',stdoutEnd:6,stderrEnd:0,stdoutDropped:0,stderrDropped:0,encodingErrors:[issue]}}
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,
 React.createElement(ToolBlock,{block}),React.createElement(TerminalCanvas,{sessionId:'owner',terminal,active:true,onUpdate:()=>{}})))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 await page.goto(server.resolvedUrls.local[0]+'__encoding-feedback')
 await page.locator('.xterm-helper-textarea').waitFor({state:'attached'})
 await page.waitForFunction(()=>window.reads===1)
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
  const message=await page.evaluate(()=>window.copy('ui.encoding.failure',{channel:'stdout',encoding:'utf-8',offset:6}))
  await page.getByRole('alert').filter({hasText:message}).first().waitFor()
  assert.equal(await page.getByRole('alert').filter({hasText:message}).count(),2)
  assert.equal(await page.evaluate(()=>window.reads),1)
  assert.equal(await page.evaluate(()=>window.writes),0)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Encoding errors: real tool preview and terminal alerts follow language, theme and width without replaying input or reads')
},{entry:'/__encoding-feedback-entry.js',vite:{plugins:[{
 name:'encoding-feedback-fixture',enforce:'pre',resolveId(id){if(id==='/__encoding-feedback-entry.js')return id},async load(id){if(id.endsWith('/TerminalWorkbench.tsx'))return new TextDecoder('utf-8',{fatal:true}).decode(await readFile(id))+'\nexport {TerminalCanvas}\n';if(id==='/__encoding-feedback-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__encoding-feedback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__encoding-feedback','<html><body><div id="root" style="height:800px"></div><script type="module" src="/__encoding-feedback-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
