import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {browserScenario} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'
import {fileURLToPath} from 'node:url'
const snapshots=JSON.parse(execFileSync(fixturePython,['-c',`
import json
from types import SimpleNamespace
from tests.test_run_recovery import RunRecoveryTests
from harness.app.runtime import RunController
from harness.app.recovery import recovery_preview
from harness.core.runs import AgentRun,turn_outcome
from harness.core.language import current_language
fixture=RunRecoveryTests();fixture.setUp()
try:
 fixture.session.events=[]
 with current_language.set('zh-CN'):
  status,error=turn_outcome(SimpleNamespace(reason='max_steps',error=None,error_info=None))
  fixture.save(AgentRun(id='restarted',session_id='session',status='running'),AgentRun(id='incomplete',session_id='session',status=status,error=error),AgentRun(id='external',session_id='session',status='failed',error={'code':'external/error','message':'Original external 原文 {reason}'}))
  controller=RunController(fixture.host);fixture.host.runtime=controller
  records=controller.history('session',summary=True)['runs']
  previews={identity:recovery_preview(fixture.host,'session',identity) for identity in ['incomplete','restarted']}
  print(json.dumps({'records':records,'previews':previews},ensure_ascii=False))
finally:fixture.doCleanups()
`],{cwd:fileURLToPath(new URL('../../',import.meta.url)),encoding:'utf8'}))
const expected={"en": {"incomplete": "Run did not complete; stop reason: max_steps", "restarted": "A service restart interrupted the task; it was not replayed automatically."}, "zh-CN": {"incomplete": "运行未完成，停止原因：max_steps", "restarted": "服务重启中断任务；未自动重放。"}}
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {RunHistoryCard} from '/src/components/inspector/RunHistoryCard.tsx'
import {RecoveryReview} from '/src/components/inspector/RecoveryReview.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
await setLocale('en');window.locale=setLocale;window.writes=0
const records=${JSON.stringify(snapshots.records)}
const view=new URLSearchParams(location.search).get('view')
const root=createRoot(document.getElementById('root'))
let showReview=false
function render(){root.render(React.createElement(React.Fragment,null,
 ...records.map(record=>React.createElement(RunHistoryCard,{key:record.id,record,sessionId:'session',busy:false,onContinue:()=>window.writes++,onOriginal:()=>window.writes++})),
 view&&showReview?React.createElement(RecoveryReview,{record:records.find(record=>record.id===view),sessionId:'session',onClose:()=>{},onSubmitted:()=>window.writes++,onOriginal:()=>window.writes++}):null))}
window.openReview=()=>{showReview=true;render()}
render()
`
await browserScenario(async({server,browser})=>{
 for(const view of ['', 'incomplete','restarted']){
  const page=await browser.newPage();page.setDefaultTimeout(5000)
  let reads=0;const errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   assert.equal(route.request().method(),'GET')
   const id=route.request().url().match(/runs\/(incomplete|restarted)\/recovery/)?.[1]
   assert.ok(id);reads++
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(snapshots.previews[id])})
  })
  await page.goto(server.resolvedUrls.local[0]+'__run-state'+(view?'?view='+view:''))
  const external=page.locator('[data-run-id="external"]')
  await external.locator('summary').filter({hasText:'Technical details'}).click()
  if(view)await page.evaluate(()=>window.openReview())
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(async({locale,theme})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',theme==='dark')},{locale,theme})
   for(const id of ['incomplete','restarted']){
    const text=expected[locale][id]
    await page.getByText(text,{exact:true}).nth(view===id?1:0).waitFor()
    assert.equal(await page.getByText(text,{exact:true}).count(),view===id?2:1)
   }
   await page.getByText('Original external 原文 {reason}',{exact:true}).waitFor()
   assert.equal(reads,view?1:0)
   assert.equal(await page.evaluate(()=>window.writes),0)
  }
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Actual run summaries and recovery previews switch language without repeating reads or writes; external diagnostics remain original')
},{entry:'/__run-state-entry.js',vite:{plugins:[{name:'run-state-fixture',resolveId(id){if(id==='/__run-state-entry.js')return id},load(id){if(id==='/__run-state-entry.js')return entry},configureServer(server){server.middlewares.use('/__run-state',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__run-state','<!doctype html><html><body><div id="root"></div><script type="module" src="/__run-state-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
