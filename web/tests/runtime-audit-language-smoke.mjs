import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {TerminalCard,SubagentCard,JobCard} from '/src/components/inspector/RuntimeTab.tsx'
import {TerminalCanvas,TerminalWorkbench} from '/src/components/inspector/TerminalWorkbench.tsx'
import {WorkspaceTabsContext} from '/src/components/inspector/WorkspaceTabs.tsx'
import {TeamMemberDetails} from '/src/components/chat/blocks/TeamMemberDetails.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {api} from '/src/lib/api.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.operations=[];window.reads=0
const view=new URLSearchParams(location.search).get('view')
const terminal={sessionId:'terminal',type:'fixture',pid:1,transport:view==='canvas-input'?'pipe':'pty',cwd:'/fixture',name:'Original terminal',status:{kind:'running'}}
const agent={agent_id:'agent',description:'Original Agent',status:'running',created_at:1,tool_calls:0,pending_messages:0,prompt:'Original prompt 原文'}
const job={id:'job',kind:'shell',label:'Original job',status:'running',startedAt:1}
let stopped=false,created=false
const terminalView=()=>stopped?{...terminal,status:{kind:'exited',exit_code:0}}:terminal
const agentView=()=>stopped?{...agent,status:'interrupted'}:agent
api.stopTerminal=async()=>{window.operations.push('terminal-stop');stopped=true;return {ok:true,data:{terminal:terminalView(),outcome:'closed',audit_error:t('ui.audit.terminalStopIncomplete')}}}
api.stopSubagent=async()=>{window.operations.push('agent-stop');stopped=true;return {ok:true,data:{agent:agentView(),audit_error:t('ui.audit.agentStopIncomplete')}}}
api.stopJob=async()=>{window.operations.push('job-stop');return {ok:true,data:{job:{...job,status:'killed'},outcome:'killed',audit_error:t('ui.audit.jobStopIncomplete')}}}
api.openTerminal=async()=>{window.operations.push('terminal-open');created=true;return {ok:true,data:{terminal,audit_error:t('ui.audit.terminalActionIncomplete')}}}
api.terminalInput=async()=>{window.operations.push('terminal-input');return {ok:true,data:{terminal:terminalView(),audit_error:t('ui.audit.terminalActionIncomplete')}}}
api.terminals=async()=>{window.reads++;return {ok:true,data:{terminals:view==='workbench-open'&&!created?[]:[terminalView()],backends:['fixture'],default_type:'fixture',enabled:true,managed_runtime:false}}}
api.terminalStream=async()=>{window.reads++;return {ok:true,data:{terminal:terminalView(),text:'Original output',cursor:15,end:15,eof:stopped,reset:false}}}
api.resizeTerminal=async()=>({ok:true,data:{}})
api.subagentDetail=async()=>{window.reads++;return {ok:true,data:{agent:agentView()}}}
api.getSession=async()=>({ok:false,status:404,message:'fixture'})
useAppStore.setState({activeSessionId:'owner',toasts:[]})
const publish=update=>{window.registry=update(window.registry||{})}
function Fixture(){
 const [term,setTerm]=useState(terminal),[member,setMember]=useState(agent),[work,setWork]=useState(job)
 const content=view==='runtime-terminal'?React.createElement(TerminalCard,{terminal:term,ownerSessionId:'owner',onUpdate:(_,value)=>setTerm(value)}):view==='runtime-agent'?React.createElement(SubagentCard,{agent:member,ownerSessionId:'owner',onUpdate:(_,value)=>setMember(value)}):view==='runtime-job'?React.createElement(JobCard,{job:work,ownerSessionId:'owner',onUpdate:(_,value)=>setWork(value)}):view==='member'?React.createElement(TeamMemberDetails,{owner:'owner',id:'agent',name:'Original Agent',embedded:true}):view.startsWith('workbench')?React.createElement(TerminalWorkbench,{sessionId:'owner'}):React.createElement(TerminalCanvas,{sessionId:'owner',terminal:term,active:true,onUpdate:setTerm})
 return React.createElement(WorkspaceTabsContext.Provider,{value:publish},React.createElement(React.Fragment,null,content,React.createElement(Toaster)))
}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
const cases=[
 ['runtime-terminal','ui.audit.terminalStopIncomplete','ui.components.inspector.RuntimeTab.2993cb23dc3e'],
 ['runtime-agent','ui.audit.agentStopIncomplete','ui.components.inspector.RuntimeTab.11820a8941c3'],
 ['runtime-job','ui.audit.jobStopIncomplete','ui.components.inspector.RuntimeTab.a33b3101a358'],
 ['member','ui.audit.agentStopIncomplete','ui.team.member.stop'],
 ['canvas-stop','ui.audit.terminalStopIncomplete','ui.components.inspector.TerminalWorkbench.2993cb23dc3e'],
 ['canvas-input','ui.audit.terminalActionIncomplete'],['canvas-pty','ui.audit.terminalActionIncomplete'],
 ['workbench-open','ui.audit.terminalActionIncomplete'],['workbench-close','ui.audit.terminalStopIncomplete'],
]
await browserScenario(async({server,browser})=>{
 for(const [view,key,button] of cases){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  await page.goto(server.resolvedUrls.local[0]+'__runtime-audit?view='+view)
  if(view.startsWith('canvas')||view==='workbench-close'){
   await page.locator('.xterm-helper-textarea').waitFor({state:'attached'})
   await page.getByText(await page.evaluate(()=>window.copy('ui.components.inspector.TerminalWorkbench.5be0323e8adc')),{exact:true}).first().waitFor()
  }
  if(button)await page.getByRole('button',{name:await page.evaluate(key=>window.copy(key),button),exact:true}).click()
  else if(view==='canvas-input'){
   const input=page.getByRole('textbox',{name:await page.evaluate(()=>window.copy('ui.components.inspector.TerminalWorkbench.a065d91d9713')),exact:true})
   await input.fill('Original input 原文');await input.press('Enter')
  }else if(view==='canvas-pty'){
   const input=page.locator('.xterm-helper-textarea');await input.waitFor({state:'attached'})
   await page.waitForFunction(()=>window.reads>0)
   await input.focus();await input.press('x');await page.clock.runFor(100)
  }else if(view==='workbench-close'){
   await page.waitForFunction(()=>window.registry?.terminal?.options.some(item=>item.value==='terminal'))
   await page.evaluate(()=>window.registry.terminal.close('terminal'))
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.terminal.confirmClose')),exact:true}).click()
  }
  const wait=async()=>page.getByText(await page.evaluate(key=>window.copy(key),key),{exact:true}).first().waitFor()
  await wait()
  if(view==='workbench-open'){
   await page.locator('.xterm-helper-textarea').waitFor({state:'attached'})
   await page.getByText(await page.evaluate(()=>window.copy('ui.components.inspector.TerminalWorkbench.5be0323e8adc')),{exact:true}).first().waitFor()
  }
  const original=await page.evaluate(()=>({operations:window.operations,reads:window.reads}))
  assert.equal(original.operations.length,1,view)
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.classList.toggle('dark',theme==='dark')},{locale,theme})
   await wait();assert.deepEqual(await page.evaluate(()=>({operations:window.operations,reads:window.reads})),original)
  }
  assert.deepEqual(errors,[]);await page.close();console.log(view+': audit resources change language without replaying operations')
 }
},{entry:'/__runtime-audit-entry.js',vite:{plugins:[{name:'runtime-audit-fixture',enforce:'pre',resolveId(id){if(id==='/__runtime-audit-entry.js')return id},async load(id){if(id.endsWith('/RuntimeTab.tsx'))return await readFile(id,'utf8')+'\nexport {TerminalCard,SubagentCard,JobCard}\n';if(id.endsWith('/TerminalWorkbench.tsx'))return await readFile(id,'utf8')+'\nexport {TerminalCanvas}\n';if(id==='/__runtime-audit-entry.js')return entry},configureServer(server){server.middlewares.use('/__runtime-audit',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__runtime-audit','<html><body><div id="root" style="height:800px"></div><script type="module" src="/__runtime-audit-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
