import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TeamMemberDetails} from '/src/components/chat/blocks/TeamMemberDetails.tsx'
import {MemberDock} from '/src/components/chat/MemberDock.tsx'
import {ChangesPanel} from '/src/components/inspector/ChangesPanel.tsx'
import {PluginHookReview} from '/src/components/overlays/PluginHooks.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
await setLocale('en');window.locale=setLocale;window.copy=t;window.reads=0;window.writes=0
api.subagentDetail=async(_owner,id)=>{window.reads++;const count=id.startsWith('status:')?0:Number(id.split('-')[1]);return {ok:true,data:{agent:{agent_id:id,status:id.startsWith('status:')?id.slice(7):'completed',created_at:1,tool_calls:count,pending_messages:count,prompt:'Original-{name} 原文',result:'Original result 原文'}}}}
createRoot(document.getElementById('root')).render(React.createElement('main',null,
 ...['pending','running','completed','failed','interrupted'].map(status=>React.createElement('section',{'data-detail-status':status,key:'detail-'+status},React.createElement(TeamMemberDetails,{owner:'owner',id:'status:'+status,name:'Original '+status,embedded:true}))),
 ...['pending','running','completed','done','failed','blocked','interrupted','error','RUNNING'].map(status=>React.createElement('section',{'data-member-status':status,key:status},React.createElement(MemberDock,{sessionId:'owner',blocks:[{kind:'subagent',id:status,agentId:status,at:1,description:'Original '+status,status}]}))),
 ...[0,1,2].map(count=>React.createElement('section',{key:count,id:'count-'+count},
 React.createElement(TeamMemberDetails,{owner:'owner',id:'member-'+count,name:'Original member '+count,embedded:true}),
 React.createElement(MemberDock,{sessionId:'owner',blocks:Array.from({length:count},(_,i)=>({kind:'subagent',id:'block-'+i,agentId:'agent-'+i,at:1,description:'Original Agent '+i,status:'completed'}))}),
 React.createElement(ChangesPanel,{sessionId:String(count),historyTarget:null}))),
 ...[false,true].map(trusted=>React.createElement(PluginHookReview,{key:String(trusted),item:{hooks:[{event:'PreToolUse',type:'command',command:'Original script 原文',timeout:1}],hooks_trusted:trusted},busy:false,onTrust:()=>window.writes++}))))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 const errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{
  const req=route.request(),url=new URL(req.url());requests.push(req.method())
  if(url.pathname.endsWith('/options'))return route.fulfill({json:{branches:['main'],current:'main',upstream:null,commits:[],next_offset:null}})
  const count=Number(url.pathname.split('/')[4])
  return route.fulfill({json:{available:true,scope:'uncommitted',left:'HEAD',right:'WORKTREE',revision:'current',workspace:'/fixture',total:count,files:Array.from({length:count},(_,i)=>({path:'/fixture/Original-'+i+'.txt',repo_path:'Original-'+i+'.txt',old_path:null,status:'modified',added:0,deleted:0})),next_offset:null,added:0,deleted:0,...(url.searchParams.has('path')?{selection:{path:url.searchParams.get('path'),patch:'',binary:false,truncated:false,conflict:false}}:{})}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__member-count')
 for(const count of [0,1,2])await page.locator('#count-'+count).getByText(count===0?'No changes.':count===1?'1 file':count+' files',{exact:true}).waitFor()
 const reads=await page.evaluate(()=>window.reads),calls=requests.length
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const status of ['pending','running','completed','failed','interrupted']){
   const section=page.locator('[data-detail-status='+status+']'),key='ui.member.status.'+(status==='completed'?'done':status)
   await section.getByText(await page.evaluate(key=>window.copy(key),key),{exact:true}).waitFor()
   await section.getByText('Original result 原文',{exact:true}).waitFor()
  }
  for(const count of [0,1,2]){
   const scope=page.locator('#count-'+count)
   await scope.getByText(locale==='en'?count+(count===1?' tool call':' tool calls'):count+' 次工具调用',{exact:true}).waitFor()
   if(count){
    await scope.getByText(locale==='en'?count+(count===1?' pending message':' pending messages'):count+' 条待处理消息',{exact:true}).waitFor()
    await scope.getByText(locale==='en'?count+(count===1?' member':' members'):count+' 位成员',{exact:true}).waitFor()
   }
   await scope.getByText(count===0?(locale==='en'?'No changes.':'没有变更。'):locale==='en'?count+(count===1?' file':' files'):count+' 个文件',{exact:true}).waitFor()
   if(count) await scope.locator(`button.changes-file-heading[aria-label*="Original-${count-1}.txt"]`).waitFor()
   if(count) await scope.locator(`button.changes-tree-file[aria-label*="Original-${count-1}.txt"]`).waitFor({state:'attached'})
   await scope.getByText('Original result 原文',{exact:true}).waitFor()
  }
  for(const trusted of [false,true]){
   const summary=page.locator('summary').filter({hasText:locale==='en'?'Hook review (1) · '+(trusted?'Trusted':'Review required'):'审核 1 个钩子 · '+(trusted?'已信任':'待审核')})
   if(!await summary.evaluate(node=>node.parentElement.open))await summary.click()
  }
  if(locale==='zh-CN'){
   await page.getByRole('button',{name:'信任当前钩子定义',exact:true}).waitFor()
   await page.getByRole('button',{name:'撤销钩子信任',exact:true}).waitFor()
  }
  for(const status of ['pending','running','completed','done','failed','blocked','interrupted','error','RUNNING']){
   const section=page.locator('[data-member-status='+JSON.stringify(status)+']')
   const button=section.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.member.toggle')),exact:true})
   if(await button.getAttribute('aria-expanded')==='false')await button.click()
   const normalized=status==='completed'?'done':['error','RUNNING'].includes(status)?'unknown':status
   const label=await page.evaluate(status=>window.copy('ui.member.status.'+status),normalized)
   assert.equal(await section.getByText(label,{exact:true}).count(),2)
   await section.getByText('Original '+status,{exact:true}).waitFor()
  }
  assert.equal(await page.evaluate(()=>window.reads),reads);assert.equal(requests.length,calls)
  assert.equal(await page.evaluate(()=>window.writes),0)
 }
 await page.clock.runFor(2000)
 await page.waitForFunction(reads=>window.reads===reads+2,reads)
 assert.equal(await page.evaluate(()=>window.writes),0)
 assert.ok(requests.every(method=>method==='GET'));assert.deepEqual(errors,[])
 console.log('Member tools/messages/dock and changes counts: 0/1/2, en/zh/en, themes/widths; Hook terms and no repeated reads/writes passed')
},{entry:'/__member-count-entry.js',vite:{plugins:[{name:'member-count-fixture',resolveId(id){if(id==='/__member-count-entry.js')return id},load(id){if(id==='/__member-count-entry.js')return entry},configureServer(server){server.middlewares.use('/__member-count',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__member-count','<html><body><div id="root"></div><script type="module" src="/__member-count-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
