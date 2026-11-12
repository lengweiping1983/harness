import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {JsonView} from '/src/components/ui/JsonView.tsx'
import {CompactionCapacityEvidence} from '/src/components/inspector/CompactionCapacityEvidence.tsx'
import {GitFileStatus} from '/src/components/overlays/GitFileStatus.tsx'
import {ExternalRunnerStatus} from '/src/components/overlays/ExternalRunnerStatus.tsx'
import {externalRunnerReadiness} from '/src/lib/externalAgents.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.actions=[]
const text='😀'.repeat(600)+'原文{value}'
const entry={id:'codex',available:true,install:'Original command 原文 {command}',docs_url:'https://fixture.invalid/docs'}
const runnerCases=[['unknown',null],['unknown-id',{runners:[],delegation_enabled:true}],['disabled',{runners:[entry],delegation_enabled:false}],['detected',{runners:[entry],delegation_enabled:true}],['missing',{runners:[{...entry,available:false}],delegation_enabled:true}]]
window.text=text;window.readiness=runnerCases.map(([name,catalog])=>[name,externalRunnerReadiness(catalog,'codex')])
const capacity={version:1,message_tokens:1001,runtime_tokens:1002,tool_tokens:1003,input_tokens:1004,context_window:null,compact_threshold:null,remaining_tokens:1007}
const audit={id:1,time:1,status:'completed',pruned_results:0,chars_removed:0,trigger:'forced',capacity_before:capacity,capacity_after:{...capacity,message_tokens:501}}
window.audit=audit
const statuses=['??','!!','UU',' M','A ','D ','R ','C ','T ','MM','XY','  ']
createRoot(document.getElementById('root')).render(React.createElement('main',{className:'space-y-4 p-3'},
 React.createElement('section',{'data-string':true},React.createElement(JsonView,{value:text})),
 React.createElement('section',{'data-array':true},React.createElement(JsonView,{value:Array.from({length:401},(_,i)=>'Original-'+i)})),
 React.createElement('section',{'data-capacity':true},React.createElement(CompactionCapacityEvidence,{row:audit})),
 ...statuses.map(status=>React.createElement('section',{'data-git':status,key:status},React.createElement(GitFileStatus,{status}))),
 ...runnerCases.map(([name,catalog])=>React.createElement('section',{'data-runner':name,key:name},React.createElement(ExternalRunnerStatus,{runner:'codex',catalog,loading:false,error:'',refresh:()=>window.actions.push('refresh')}))),
 React.createElement('section',{'data-runner':'loading'},React.createElement(ExternalRunnerStatus,{runner:'codex',catalog:null,loading:true,error:'',refresh:()=>window.actions.push('refresh')})),
 React.createElement('section',{'data-runner':'error'},React.createElement(ExternalRunnerStatus,{runner:'codex',catalog:null,loading:false,error:'Original diagnostic 原文 {error}',refresh:()=>window.actions.push('refresh')}))))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[],warnings=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')warnings.push(message.text())})
 await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500})})
 await page.goto(server.resolvedUrls.local[0]+'__structured-resource-chain')
 page.setDefaultTimeout(10000)
 await page.locator('[data-capacity] summary').click()
 const audit=await page.evaluate(()=>window.audit)
 const text=await page.evaluate(()=>window.text),points=Array.from(text)
 const string=page.locator('[data-string]'),array=page.locator('[data-array]')
 const expectedGit={'??':['untracked'],'!!':['ignored'],UU:['conflict'],' M':['modified'],'A ':['added','staged'],'D ':['deleted','staged'],'R ':['renamed','staged'],'C ':['copied','staged'],'T ':['typeChanged','staged'],MM:['modified','partial'],XY:['unknown','partial'],'  ':['unknown']}
 assert.deepEqual(await page.evaluate(()=>window.readiness),[['unknown','unknown'],['unknown-id','unknown'],['disabled','disabled'],['detected','detected'],['missing','missing']])
 const label=action=>page.evaluate(action=>window.copy('ui.json.'+action+'For',{path:'$'}),action)
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  await string.getByText(await page.evaluate(total=>window.copy('ui.json.range',{start:1,end:600,total}),points.length),{exact:true}).waitFor()
  assert.equal(await string.locator('[data-json-text]').textContent(),points.slice(0,600).join(''))
  for(const action of ['first','previous','next','last']){
   const button=string.getByRole('button',{name:await label(action),exact:true})
   await button.waitFor();assert.equal(await button.textContent(),await page.evaluate(action=>window.copy('ui.json.'+action),action))
  }
  assert.equal(await string.getByRole('button',{name:await label('first'),exact:true}).isDisabled(),true)
  await string.getByRole('button',{name:await label('last'),exact:true}).click()
  assert.equal(await string.locator('[data-json-text]').textContent(),points.slice(600).join(''))
  assert.equal(await string.getByRole('button',{name:await label('next'),exact:true}).isDisabled(),true)
  await string.getByRole('button',{name:await label('previous'),exact:true}).click()
  await string.getByRole('button',{name:await label('next'),exact:true}).click()
  await string.getByRole('button',{name:await label('first'),exact:true}).click()
  await array.getByRole('button',{name:await label('last'),exact:true}).click()
  assert.deepEqual(await array.locator('[data-json-text]').allTextContents(),['Original-400'])
  await array.getByRole('button',{name:await label('first'),exact:true}).click()
  assert.equal(await array.locator('[data-json-text]').count(),200)
  for(const [status,keys] of Object.entries(expectedGit)){
   const section=page.locator('[data-git]').filter({has:page.locator('[title='+JSON.stringify(status)+']')})
   const expected=await page.evaluate(keys=>keys.map(key=>window.copy('ui.git.status.'+key)),keys)
   await page.waitForFunction(({status,expected})=>{const node=[...document.querySelectorAll('[data-git]')].find(node=>node.dataset.git===status);return JSON.stringify([...node.querySelectorAll('[title] > span')].map(node=>node.textContent))===JSON.stringify(expected)},{status,expected})
   assert.deepEqual(await section.locator('[title] > span').allTextContents(),expected)
  }
  for(const state of ['unknown','unknown-id','disabled','detected','missing','loading','error']){
   const key=state==='unknown-id'?'unknown':state==='loading'?'checking':state==='error'?'failed':state
   const expected=await page.evaluate(key=>window.copy('ui.runnerStatus.'+key,{error:'Original diagnostic 原文 {error}'}),key)
   await page.locator('[data-runner='+JSON.stringify(state)+']').getByRole('status').getByText(expected,{exact:true}).waitFor()
  }
  const capacityKeys=['ui.components.inspector.CompactionCapacityEvidence.a9882de5cd3a','ui.components.inspector.CompactionCapacityEvidence.381da5690a2e','ui.context.category.tools','ui.components.inspector.CompactionCapacityEvidence.3efc8c8c6385','ui.components.inspector.CompactionCapacityEvidence.acdd83bdc00f','ui.components.inspector.CompactionCapacityEvidence.ee4485e976f2','ui.components.inspector.CompactionCapacityEvidence.1410a27bf4fc']
  for(const key of capacityKeys)await page.locator('[data-capacity]').getByRole('rowheader',{name:await page.evaluate(key=>window.copy(key),key),exact:true}).waitFor()
  assert.deepEqual(await page.evaluate(()=>window.audit),audit)
  assert.deepEqual(await page.evaluate(()=>window.actions),[])
 }
 assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);assert.deepEqual(warnings,[])
 await page.close();console.log('Structured resources: Unicode pagination/actions, Git finite statuses, runner readiness preserve raw data across language/theme/width changes')
},{entry:'/__structured-resource-chain-entry.js',vite:{plugins:[{name:'structured-resource-chain',resolveId(id){if(id==='/__structured-resource-chain-entry.js')return id},load(id){if(id==='/__structured-resource-chain-entry.js')return entry},configureServer(server){server.middlewares.use('/__structured-resource-chain',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__structured-resource-chain','<html><body><div id="root"></div><script type="module" src="/__structured-resource-chain-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
