import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ThinkingBlock,CompactionBlock,TeamBlock,TurnBlock,ErrorBlock} from '/src/components/chat/blocks/MessageBlocks.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import {readTeamProgress} from '/src/lib/teamProgress.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const base={at:0,id:'fixture'}
const specs=[['thinking',ThinkingBlock,{...base,kind:'thinking',text:'A😀中'}],['compaction',CompactionBlock,{...base,kind:'compaction',phase:'end',completedChunks:2,totalChunks:3,pruned:1,charsRemoved:1234}],['empty',CompactionBlock,{...base,kind:'compaction',phase:'end',completedChunks:0,totalChunks:0,pruned:0,charsRemoved:0}],['team',TeamBlock,{...base,kind:'team',team:'Original team 原文',runId:'run',phase:'done',members:[],task:'Original task 原文'}],['start',TurnBlock,{...base,kind:'turn',phase:'start',turn:7}],['startUnknown',TurnBlock,{...base,kind:'turn',phase:'start'}],...['historical','current','withoutTurn'].map(mode=>['error-'+mode,ErrorBlock,{...base,kind:'error',message:'Original error 原文',historical:mode!=='current',...(mode==='withoutTurn'?{}:{turn:7})}]),...['historical','current','withoutTurn'].map(mode=>['status-'+mode,TurnBlock,{...base,kind:'turn',phase:'end',reason:'blocked',historical:mode!=='current',...(mode==='withoutTurn'?{}:{turn:7})}])]
specs.push(['unknownTeam',TeamBlock,readTeamProgress({run_id:'unknown-status',phase:'execute',team:'Original team',members:['constructor','toString','__proto__','ordinary-unknown'].map(status=>({name:status,role:'member',status}))})])
window.specs=specs;window.original=JSON.stringify(specs.map(row=>row[2]))
createRoot(document.getElementById('root')).render(React.createElement('main',null,...specs.map(([id,Component,block])=>React.createElement('section',{key:id,'data-message-summary':id},React.createElement(Component,{block})))))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[];page.setDefaultTimeout(15000)
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
 await page.goto(server.resolvedUrls.local[0]+'__message-summary')
 const rows=[['thinking','ui.components.chat.blocks.MessageBlocks.a62acb184f47',{}],['compaction','ui.message.compactionSections',{completed:2,total:3}],['compaction','ui.message.prunedItems',{count:1}],['compaction','ui.message.removedCharacters',{count:'1.2k'}],['team','ui.message.teamTask',{task:'Original task 原文'}],['start','ui.message.turnNumber',{turn:7}],['startUnknown','ui.message.turnStart',{}],['error-historical','ui.message.historicalFailureTurn',{turn:7}],['error-current','ui.message.failureTurn',{turn:7}],['error-withoutTurn','ui.components.chat.blocks.MessageBlocks.590cdc817334',{}],['status-historical','ui.message.historicalStatusTurn',{turn:7}],['status-current','ui.message.statusTurn',{turn:7}],['status-withoutTurn','ui.components.chat.blocks.MessageBlocks.7345d4847180',{}]]
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const [id,key,values] of rows){const expected=await page.evaluate(({key,values})=>window.copy(key,values),{key,values});await page.locator('[data-message-summary="'+id+'"]').getByText(expected,{exact:true}).waitFor()}
  const unknown=await page.evaluate(()=>window.copy('ui.member.status.unknown'))
  assert.equal(await page.locator('[data-message-summary="unknownTeam"]').getByText(unknown,{exact:true}).count(),4)
  assert.equal(await page.locator('[data-message-summary="unknownTeam"] [aria-hidden]').filter({hasText:'…'}).count(),4)
  const thought=page.locator('[data-message-summary="thinking"] details')
  assert.equal(/characters|字符/i.test(await thought.locator('summary').innerText()),false)
  await thought.locator('summary').click()
  assert.equal(await thought.locator('.md').innerText(),'A😀中')
  await thought.locator('summary').click()
  if(locale==='en'){
   await page.locator('[data-message-summary="start"]').getByText('Turn 7',{exact:true}).waitFor()
   await page.locator('[data-message-summary="team"]').getByText('Task: Original task 原文',{exact:true}).waitFor()
  }
  for(const mode of ['historical','current','withoutTurn'])await page.locator('[data-message-summary="error-'+mode+'"]').getByText('Original error 原文',{exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>JSON.stringify(window.specs.map(row=>row[2]))===window.original),true)
  assert.equal(await page.locator('[data-message-summary="empty"]').innerText(),await page.evaluate(()=>window.copy('ui.components.chat.blocks.MessageBlocks.72a4f36264be')))
 }
 assert.deepEqual(errors,[]);assert.deepEqual(requests,[])
 console.log('Message summaries: thinking omits character counts and preserves Unicode original; compaction, tasks and current/historical turns retain bilingual templates across themes and widths')
},{entry:'/__message-summary-entry.js',vite:{plugins:[{name:'message-summary-fixture',resolveId(id){if(id==='/__message-summary-entry.js')return id},load(id){if(id==='/__message-summary-entry.js')return entry},configureServer(server){server.middlewares.use('/__message-summary',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__message-summary','<html><body><div id="root"></div><script type="module" src="/__message-summary-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
