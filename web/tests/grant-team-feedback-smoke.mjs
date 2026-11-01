import {readFile} from 'node:fs/promises'
import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TemporaryGrants} from '/src/components/inspector/TemporaryGrants.tsx'
import {BrowserActivityBar} from '/src/components/inspector/BrowserActivityBar.tsx'
import {TeamIntegration} from '/src/components/chat/blocks/TeamIntegration.tsx'
import {TeamMemberDetails} from '/src/components/chat/blocks/TeamMemberDetails.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[]
const view=new URLSearchParams(location.search).get('view')
const failure={ok:false,status:200,code:'response/invalid-json',message:'snapshot',copy:{key:'ui.http.invalidResponse'}}
const agent={agent_id:'member',child_session_id:'child',status:'running',created_at:1,tool_calls:0,pending_messages:0,prompt:'Task 原文'}
api.subagentDetail=async()=>{window.calls.push('load');return view==='member-load'?failure:{ok:true,data:{agent:{...agent,...(view==='member-identity'?{agent_id:'other'}:{})}}}}
api.getSession=async()=>failure
api.stopSubagent=async()=>{window.calls.push('stop');return view==='member-stop-identity'?{ok:true,data:{agent:{...agent,agent_id:'other'}}}:failure}
const block={phase:'done',runId:'run',sourceSeq:1,worktrees:[{team_member:'worker'}],members:[{name:'worker',status:'done',delivery:{tree:'hash'}}]}
const content=view.startsWith('member')?React.createElement(TeamMemberDetails,{owner:'owner',id:'member',name:'Worker 原文',embedded:true}):view==='integration'?React.createElement(TeamIntegration,{sessionId:'owner',block}):view==='browser'?React.createElement(BrowserActivityBar,{sessionId:'owner',pageId:'page',active:true}):React.createElement(TemporaryGrants,{sessionId:'owner'})
createRoot(document.getElementById('root')).render(content)
`
const auditWarning=JSON.parse(await readFile(new URL('../src/i18n/locales/en.json',import.meta.url),'utf8'))['ui.permissions.auditIncomplete']
const grant=id=>({id,tool:id==='grant'?'fixture_tool':'other_tool',cwd:'/User 原文',call_id:id,remaining_seconds:60})
await browserScenario(async({server,browser})=>{
 for(const view of ['grant-empty','grant-audit','grants-read','grant-detail','grant-unknown','grant-mismatch','grant-decision','integration','member-load','member-identity','member-stop','member-stop-identity','browser']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const requests=[],errors=[];let revoked=false;page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),path=new URL(request.url()).pathname;requests.push({method:request.method(),path})
   const broken=()=>route.fulfill({status:200,body:'broken',contentType:'application/json'})
   if(request.method()==='DELETE'){
    if(view==='grant-unknown')return broken()
    if(view==='grant-decision'||view==='grant-audit'){revoked=true;return route.fulfill({json:{revoked:'grant',...(view==='grant-audit'?{audit_error:auditWarning}:{})}})}
    return route.fulfill({json:{revoked:'other'}})
   }
   if(path.endsWith('/permissions/grants')&&view!=='grants-read')return route.fulfill({json:{scope:'owner',parent_controlled:false,grants:view==='grant-empty'?[]:revoked?[grant('other')]:[grant('grant'),grant('other')]}})
   return broken()
  })
  await page.goto(server.resolvedUrls.local[0]+'__grant-team-feedback?view='+view)
  const prefix='ui.components.inspector.TemporaryGrants.'
  if(view==='grant-detail')await page.locator('summary').first().click()
  let refreshed
  if(['grant-unknown','grant-mismatch','grant-decision','grant-audit'].includes(view)){
   refreshed=page.waitForResponse(response=>response.url().endsWith('/permissions/grants'))
   await page.getByRole('button',{name:await page.evaluate(prefix=>window.copy(prefix+'bf368f79fae5'),prefix),exact:true}).first().click()
  }
  if(view==='integration')await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.team.integration.create')),exact:true}).click()
  if(view.startsWith('member-stop'))await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.team.member.stop')),exact:true}).click()
  const expected=()=>page.evaluate(({view,prefix})=>view==='grant-empty'?window.copy('ui.permissions.grantCount',{count:0}):view==='grant-audit'?window.copy('ui.permissions.auditIncomplete'):view==='grant-decision'?window.copy(prefix+'27f5da006428'):view.includes('identity')?window.copy('ui.team.member.identityMismatch'):view==='grant-mismatch'?window.copy(prefix+'9caf4baa9699',{detail:window.copy('ui.permissions.grantReceiptMismatch')}):window.copy('ui.http.invalidResponse'),{view,prefix})
  const wait=async()=>page.waitForFunction(expected=>document.body.textContent.includes(expected),await expected())
  await wait()
  if(refreshed)await refreshed
  const count=requests.length,calls=await page.evaluate(()=>window.calls.length)
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await wait();assert.equal(requests.length,count,'locale must not repeat requests');assert.equal(await page.evaluate(()=>window.calls.length),calls)
   if(view.startsWith('grant-')||view==='grants-read'){
    const grants=view==='grant-empty'?0:['grant-audit','grant-decision'].includes(view)?1:view==='grants-read'?null:2
    const title=grants===null?await page.evaluate(prefix=>window.copy(prefix+'21391506e2ee'),prefix):await page.evaluate(count=>window.copy(count===1?'ui.permissions.grantCountOne':'ui.permissions.grantCount',{count}),grants)
    assert.equal(await page.locator('h3').first().innerText(),title)
    assert.ok(!title.includes('·'))
   }
   if(view==='grant-mismatch'){
    const text=await page.locator('body').textContent();assert.ok(text.includes('fixture_tool'));assert.ok(text.includes('other_tool'))
    assert.ok(!text.includes(await page.evaluate(prefix=>window.copy(prefix+'27f5da006428'),prefix)))
   }
  }
  if(view.startsWith('grant-')&&!['grant-empty','grant-detail'].includes(view))assert.equal(requests.filter(request=>request.method==='DELETE').length,1)
  if(view.startsWith('member-stop'))assert.equal(await page.evaluate(()=>window.calls.filter(call=>call==='stop').length),1)
  assert.deepEqual(errors,[]);await page.close();console.log(view+': language follows resources, user text and write identities preserved')
 }
},{entry:'/__grant-team-feedback-entry.js',vite:{plugins:[{
 name:'grant-team-feedback-fixture',resolveId(id){if(id==='/__grant-team-feedback-entry.js')return id},load(id){if(id==='/__grant-team-feedback-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__grant-team-feedback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__grant-team-feedback','<html><body><div id="root"></div><script type="module" src="/__grant-team-feedback-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
