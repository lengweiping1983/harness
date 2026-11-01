import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {browserScenario} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const questions=JSON.parse(execFileSync(fixturePython,['-c',`
import asyncio,json,tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from harness.core.language import current_language
from harness.llm.types import LlmCallConfig
from tests.test_permission_approval_reasons import approval_fixture
async def main():
 rows=[]
 with tempfile.TemporaryDirectory() as directory,current_language.set('zh-CN'):
  cases=[{'approval_required':True},{'dangerous':True},{'effect':'external_write'},{'effect':'execute'},
   {'name':'mcp__fixture','effect':'unknown'},{'name':'browser','args':{'action':'click'}},{'name':'web_fetch'},
   {'effect':'unknown'},{'name':'write','effect':'workspace_write','args':{'path':str(Path(directory).parent/'outside.txt')}},
   {'policy_name':'read_only','effect':'workspace_write'}]
  for case in cases:
   host,_,_,registry,execute,context,call=approval_fixture(**case)
   await registry.execute_call(call,replace(context,cwd=directory))
   execute.assert_not_awaited();rows.append(host.questions.ask.await_args.kwargs['questions'][0])
  for review in ('confirmation_requested','unavailable','input_too_large','budget_unavailable'):
   host,_,session,registry,execute,context,call=approval_fixture('auto_review',effect='execute',args={'text':'x'*25000} if review=='input_too_large' else {})
   host.adapter.complete.return_value=SimpleNamespace(text=json.dumps({'decision':'ask','reason':'Original reviewer 原文'}),has_tool_use=False,usage=None)
   if review=='unavailable':host.adapter.complete.return_value.text='invalid'
   if review=='budget_unavailable':host._agents[session.id]=SimpleNamespace(config=LlmCallConfig(model='fixture'),budget=SimpleNamespace(acquire=AsyncMock(return_value=None)),status=SimpleNamespace(status='tool'))
   await registry.execute_call(call,replace(context,cwd=directory))
   execute.assert_not_awaited();rows.append(host.questions.ask.await_args.kwargs['questions'][0])
 print(json.dumps(rows,ensure_ascii=False))
asyncio.run(main())
`],{cwd:fileURLToPath(new URL('../../',import.meta.url)),encoding:'utf8'}))
assert.equal(questions.length,14)
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QuestionCard} from '/src/components/chat/blocks/QuestionCard.tsx'
import {responseIssue} from '/src/lib/responseValidation.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.answers=[]
const snapshots=${JSON.stringify(questions)},root=createRoot(document.getElementById('root'))
window.reset=(index,viewOnly=false)=>{
 const question=snapshots[index]
 const issue=responseIssue('/api/v1/sessions/owner/questions','GET',200,{questions:[{question_id:'approval',session_id:'owner',revision:'fixture',questions:[question]}]})
 if(issue)throw Error('Generated contract rejected host approval: '+issue)
 const block={id:'approval',kind:'question',at:0,questionId:'approval-'+index,questions:[question]}
 root.render(<QuestionCard key={index+':'+viewOnly} block={block} sessionId="owner" readOnly={viewOnly} onAnswer={(id,answers)=>window.answers.push({id,answers})}/> )
}
window.reset(0)
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(), errors=[],requests=[],failedRequests=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('requestfailed',request=>failedRequests.push({url:request.url(),error:request.failure()?.errorText}))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:503,json:{error:'Unexpected fixture request'}})})
 try{await page.goto(server.resolvedUrls.local[0]+'__permission-reason-language')}
 catch(error){throw new Error('Approval fixture did not become ready: '+JSON.stringify({errors,failedRequests}),{cause:error})}
 const copy=(key)=>page.evaluate(key=>window.copy(key),key)
 for(let index=0;index<questions.length;index++){
  const question=questions[index],approval=question.operation.approval
  await page.evaluate(index=>window.reset(index),index)
  const baseline=await page.evaluate(()=>window.answers.length)
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440])for(const font of [10,22]){
   await page.setViewportSize({width,height:1100})
   await page.evaluate(({locale,dark,font})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark);document.documentElement.style.fontSize=font+'px'},{locale,dark,font})
   await page.getByText(await copy('ui.permissions.approvalReason'),{exact:true}).waitFor()
   await page.getByText(await copy('ui.permissions.approvalTitle'),{exact:true}).waitFor()
   await page.getByText(await copy('ui.permissions.approvalHint'),{exact:true}).waitFor()
   await page.getByText(await copy('ui.permissions.approvalPolicy.'+approval.policy),{exact:true}).waitFor()
   for(const reason of approval.reasons)await page.getByText(await copy('ui.permissions.reason.'+reason),{exact:true}).waitFor()
   if(approval.review)await page.getByText(await copy('ui.permissions.review.'+approval.review),{exact:true}).waitFor()
   assert.equal(await page.locator('pre').innerText(),question.question,'Original operation text must remain unchanged')
   assert.equal(await page.evaluate(()=>window.answers.length),baseline)
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  }
  if(index===0&&process.env.HARNESS_TEST_SCREENSHOT){await page.setViewportSize({width:390,height:1100});await page.screenshot({path:process.env.HARNESS_TEST_SCREENSHOT})}
  const action=index%3,keys=['f9881e36e9cd','4e9b6b4d7c21','136de7a8c46f']
  await page.getByRole('button',{name:await copy('ui.components.chat.blocks.QuestionCard.'+keys[action]),exact:true}).focus()
  await page.keyboard.press('Enter')
  await page.waitForFunction(baseline=>window.answers.length===baseline+1,baseline)
  assert.deepEqual(await page.evaluate(()=>window.answers.at(-1).answers),[{id:'permission',selected:[question.options[action].label]}])
  await page.evaluate(index=>window.reset(index,true),index)
  await page.getByText(await copy('ui.permissions.approvalReason'),{exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>window.answers.length),baseline+1)
 }
 assert.deepEqual(errors,[]);assert.deepEqual(requests,[])
 await page.close();console.log('Host approval: 10 admission reasons, four actual review fallbacks, generated response contracts, original operation, read-only shared view, keyboard actions and English/Chinese/English theme/width/font matrix passed without repeated decisions')
},{entry:'/__permission-reason-language-entry.jsx',vite:{plugins:[{
 name:'permission-reason-language-fixture',resolveId(id){if(id==='/__permission-reason-language-entry.jsx')return id},load(id){if(id==='/__permission-reason-language-entry.jsx')return entry},
 configureServer(server){server.middlewares.use('/__permission-reason-language',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__permission-reason-language','<html><body><div id="root"></div><script type="module" src="/__permission-reason-language-entry.jsx"></script></body></html>'))}catch(error){next(error)}})},
}]}})
