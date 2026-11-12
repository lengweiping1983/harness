import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {readFile} from 'node:fs/promises'
import {fixturePython} from './python-fixture.mjs'
import {browserScenario} from './browser-scenario.mjs'

const snapshots=JSON.parse(execFileSync(fixturePython,['-c',`
import json,tempfile
from pathlib import Path
from unittest.mock import Mock
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.core.runs import AgentRun
from harness.subagent.archive import SubagentArchive
from harness.subagent.manager import SubagentManager
with tempfile.TemporaryDirectory() as directory, current_language.set('zh-CN'):
 archive=SubagentArchive(Path(directory)/'runs.sqlite3')
 archive.save(AgentRun(id='recovered',parent_session_id='owner',status='running'))
 archive=SubagentArchive(archive.path)
 recovered=archive.get('recovered')
 manager=SubagentManager(adapter=None,tools=None,prompts=None)
 manager.archive=Mock()
 manager.archive.save.side_effect=OSError('External archive 原文')
 failed=AgentRun(id='archive-failed',parent_session_id='owner',status='failed')
 manager._save(failed)
 external=AgentRun(id='external',parent_session_id='owner',status='failed',error={'code':'external/error','message':'Original external 用户原文 {value}'})
 combined=AgentRun(id='combined',parent_session_id='owner',status='failed',error=HarnessError(recovered['error'],code='run/restarted',copy={'key':'ui.agent.restarted'}).to_dict())
 manager._failed(combined,OSError('External cleanup 原文'),'fixture cleanup failed %s')
 print(json.dumps([recovered,failed.to_dict(),external.to_dict(),combined.to_dict()],ensure_ascii=False))
`],{encoding:'utf8'}))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {SubagentCard} from '/src/components/inspector/RuntimeTab.tsx'
import {SubagentBlock} from '/src/components/chat/blocks/MessageBlocks.tsx'
import {TeamMemberDetails} from '/src/components/chat/blocks/TeamMemberDetails.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import {useAppStore} from '/src/store/app.ts'
import {emptySession,reduceEvent} from '/src/store/chat.ts'
import {agentTree,filterAgentTree} from '/src/lib/subagentHistory.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en');window.reads=0;window.writes=0
const rows=${JSON.stringify(snapshots)}
window.search=query=>filterAgentTree(agentTree(rows),'all',query).map(row=>row.agent.agent_id)
api.subagentDetail=async(owner,id)=>{window.reads++;return {ok:true,data:{agent:{...rows.find(row=>row.agent_id===id),prompt:'Original prompt 原文'}}}}
api.stopSubagent=async()=>{window.writes++;throw Error('Unexpected stop')}
useAppStore.setState({activeSessionId:'owner'})
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,
 ...['pending','running','completed','failed','interrupted','done','error','RUNNING'].map((status,index)=>React.createElement('section',{key:status,id:'status-'+index},...['subagent/status','subagent/message'].map(type=>{const initial=emptySession();const created={...initial,...reduceEvent(initial,{type,agent_id:status,description:'Original description 原文',status:'running'})};const updated={...created,...reduceEvent(created,{type,agent_id:status,status})};if(updated.blocks.length!==1||updated.blocks[0].status!==status)throw Error('Status reducer lost event identity');return React.createElement(SubagentBlock,{key:type,block:updated.blocks[0]})}))),
 ...rows.map(agent=>React.createElement('section',{key:agent.agent_id,id:agent.agent_id},React.createElement(SubagentCard,{agent,ownerSessionId:'owner',onUpdate:()=>{}}))),
 ...[false,true].flatMap(embedded=>rows.slice(0,2).map(agent=>React.createElement(TeamMemberDetails,{key:agent.agent_id+embedded,owner:'owner',id:agent.agent_id,name:agent.agent_id,embedded})))))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__agent-recovery')
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.team.member.open',{name:'recovered'})),exact:true}).click()
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.team.member.open',{name:'archive-failed'})),exact:true}).click()
 await page.waitForFunction(()=>window.reads===4)
 const reads=await page.evaluate(()=>window.reads)
 const keys=['ui.agent.restarted','ui.agent.archiveFailed']
 const catalogs=Object.fromEntries(await Promise.all(['en','zh-CN'].map(async locale=>[locale,JSON.parse(await readFile(new URL('../../harness/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))])))
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.classList.toggle('dark',theme==='dark')},{locale,theme})
  for(const key of keys){
   const text=catalogs[locale][key]
   await page.getByText(text,{exact:true}).nth(2).waitFor()
   assert.equal(await page.getByText(text,{exact:true}).count(),3)
  }
  const combined=catalogs[locale]['ui.agent.errorDetail'].replace('{label}','run/restarted').replace('{message}',catalogs[locale]['ui.agent.restarted'])+catalogs[locale]['ui.agent.errorSeparator']+catalogs[locale]['ui.agent.errorDetail'].replace('{label}','OSError').replace('{message}','External cleanup 原文')
  await page.getByText(combined,{exact:true}).waitFor()
  assert.deepEqual((await page.evaluate(query=>window.search(query),catalogs[locale]['ui.agent.restarted'])).sort(),['combined','recovered'])
  for(const [index,status] of ['pending','running','completed','failed','interrupted','done','error','RUNNING'].entries()){
   const label=await page.evaluate(status=>window.copy('ui.member.status.'+(['done','error','RUNNING'].includes(status)?'unknown':status==='completed'?'done':status)),status)
   const labels=page.locator('#status-'+index).getByText(label,{exact:true})
   await labels.first().waitFor();assert.equal(await labels.count(),2)
   assert.equal(await page.locator('#status-'+index).getByText('Original description 原文',{exact:true}).count(),2)
  }
  await page.getByText('Original external 用户原文 {value}',{exact:true}).waitFor()
 }
 assert.equal(await page.evaluate(()=>window.writes),0)
 assert.equal(await page.evaluate(()=>window.reads),reads)
 assert.deepEqual(errors,[])
 console.log('Actual recovered and archive-failed Agent snapshots retain bilingual resource identity in runtime and member detail without replaying writes')
},{entry:'/__agent-recovery-entry.js',vite:{plugins:[{name:'agent-recovery-fixture',enforce:'pre',resolveId(id){if(id==='/__agent-recovery-entry.js')return id},async load(id){if(id.endsWith('/RuntimeTab.tsx'))return await readFile(id,'utf8')+'\nexport {SubagentCard}\n';if(id==='/__agent-recovery-entry.js')return entry},configureServer(server){server.middlewares.use('/__agent-recovery',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__agent-recovery','<html><body><div id="root"></div><script type="module" src="/__agent-recovery-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
