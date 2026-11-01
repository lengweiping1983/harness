import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {CodeBlock} from '/src/components/markdown/CodeBlock.tsx'
import {RuntimeTab,TerminalCard,SubagentCard} from '/src/components/inspector/RuntimeTab.tsx'
import {TaskPanel} from '/src/components/inspector/TaskPanel.tsx'
import {api} from '/src/lib/api.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.reads=0
const queue=new URLSearchParams(location.search).get('view')==='queue'
useAppStore.setState({activeSessionId:queue?null:'owner'})
const member=(count)=>({agent_id:'agent-'+count,description:'Original Agent '+count,status:'completed',created_at:1,duration_ms:0,tool_calls:count,pending_messages:count})
const agents=[{...member(0),agent_id:'parent'}, {...member(1),agent_id:'child',parent_agent_id:'parent'}, {...member(2),agent_id:'grandchild',parent_agent_id:'child'}]
api.runtime=async()=>{window.reads++;return {ok:true,data:{terminals:[],subagents:agents,jobs:[]}}}
api.terminalTail=async(_owner,id)=>{window.reads++;return {ok:true,data:{totalLines:Number(id),text:'Original-{name} 原文',pendingText:id==='2'?'Progress 原文':'',truncated:id==='2'}}}
createRoot(document.getElementById('root')).render(queue?React.createElement(TaskPanel,{mode:'queue'}):React.createElement('main',null,
 ...['pending','running','completed','failed','interrupted'].map(status=>React.createElement('section',{'data-agent-status':status,key:status},React.createElement(SubagentCard,{agent:{...member(0),agent_id:status,status},ownerSessionId:null,onUpdate:()=>{}}))),
 ...[1,2].map(count=>React.createElement(CodeBlock,{key:'code-'+count,code:count===1?'Original-{name} 原文':'Original-{name} 原文\\nsecond'})),
 ...[0,1,2].map(count=>React.createElement('section',{key:count,id:'count-'+count},
 React.createElement(TerminalCard,{terminal:{sessionId:String(count),name:'Terminal '+count,cwd:'/fixture',pid:1,status:{kind:'exited',exit_code:0}},ownerSessionId:null,onUpdate:()=>{}}),
 React.createElement(SubagentCard,{agent:member(count),ownerSessionId:null,onUpdate:()=>{}}))),
 React.createElement('section',{id:'tree'},React.createElement(RuntimeTab))))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 await page.goto(server.resolvedUrls.local[0]+'__runtime-count')
 for(const count of [0,1,2])await page.locator('#count-'+count+' [data-terminal-id] > button[aria-expanded]').click()
 await page.getByText('Total: 2 lines · Output without a trailing newline · Showing only the most recent output',{exact:true}).waitFor()
 await page.locator('#tree').getByRole('button',{name:'Expand branch (2 loaded descendants)',exact:true}).click()
 await page.locator('#tree').getByRole('button',{name:'Expand branch (1 loaded descendant)',exact:true}).click()
 await page.locator('#tree [data-agent-id=grandchild]').waitFor()
 const reads=await page.evaluate(()=>window.reads)
 const empty=await browser.newPage();empty.setDefaultTimeout(5000)
 empty.on('pageerror',error=>errors.push(error.message))
 await empty.goto(server.resolvedUrls.local[0]+'__runtime-count?view=queue')
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const count of [1,2])await page.locator('figcaption').getByText(locale==='en'?(count===1?'1 line':'2 lines'):count+' 行',{exact:true}).waitFor()
  for(const count of [0,1,2]){
   const card=page.locator('#count-'+count)
   const total=locale==='en'?('Total: '+count+(count===1?' line':' lines')):('共 '+count+' 行')
   await card.getByText(total+(count===2?(locale==='en'?' · Output without a trailing newline · Showing only the most recent output':' · 有未换行输出 · 仅显示最近的输出'):''),{exact:true}).waitFor()
   await card.getByText(locale==='en'?(count+(count===1?' tool call':' tool calls')):count+' 次工具调用',{exact:true}).waitFor()
   if(count)await card.getByText(locale==='en'?(count+(count===1?' pending message':' pending messages')):count+' 条待处理消息',{exact:true}).waitFor()
   await card.getByText('Original-{name} 原文'+(count===2?'\nProgress 原文':''),{exact:true}).waitFor()
  }
  for(const status of ['pending','running','completed','failed','interrupted']){
   const key='ui.member.status.'+(status==='completed'?'done':status)
   await page.locator('[data-agent-status='+status+']').getByText(await page.evaluate(key=>window.copy(key),key),{exact:true}).waitFor()
  }
  const views=page.locator('#tree').getByRole('group',{name:await page.evaluate(()=>window.copy('ui.runtime.agents.views')),exact:true})
  for(const view of ['all','active','attention','all']){
   const text=await page.evaluate(view=>window.copy('ui.runtime.agents.'+view),view)
   const button=views.getByRole('button').filter({hasText:text});await button.click();assert.equal(await button.getAttribute('aria-pressed'),'true')
  }
  const tree=page.locator('#tree')
  const collapse=count=>locale==='en'?'Collapse branch ('+count+' loaded '+(count===1?'descendant':'descendants')+')':'收起分支（已载入 '+count+' 个后代）'
  const expand=count=>locale==='en'?'Expand branch ('+count+' loaded '+(count===1?'descendant':'descendants')+')':'展开分支（已载入 '+count+' 个后代）'
  await tree.getByRole('button',{name:collapse(1),exact:true}).click()
  await tree.getByRole('button',{name:expand(1),exact:true}).click()
  await tree.getByRole('button',{name:collapse(2),exact:true}).click()
  await tree.getByRole('button',{name:expand(2),exact:true}).click()
  await empty.setViewportSize({width,height:900})
  await empty.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  await empty.getByText(locale==='en'?'Select a conversation to view its task queue and task history.':'选择一个对话后查看任务队列与任务历史。',{exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>window.reads),reads,'Presentation must not restart reads')
 }
 assert.deepEqual(errors,[])
 console.log('Code/runtime counts, branch controls and conversation empty state: en/zh/en, themes, widths, original output and no repeated reads passed')
},{entry:'/__runtime-count-entry.js',vite:{plugins:[{name:'runtime-count-fixture',enforce:'pre',resolveId(id){if(id==='/__runtime-count-entry.js')return id},async load(id){if(id.endsWith('/RuntimeTab.tsx'))return await readFile(id,'utf8')+'\nexport {TerminalCard,SubagentCard}\n';if(id==='/__runtime-count-entry.js')return entry},configureServer(server){server.middlewares.use('/__runtime-count',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__runtime-count','<html><body><div id="root"></div><script type="module" src="/__runtime-count-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
