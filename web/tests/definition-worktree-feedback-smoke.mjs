import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const repository={root:'/fixture',branch:'main',refs:['main'],head:'a'.repeat(40),index_revision:'revision',conflicts:false,staged_files:[],files:[]}
const archived={id:'worktree',repo:'/fixture',path:'/fixture-worktree',branch:'branch',status:'archived'}
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AgentDefinitionCreator} from '/src/components/overlays/AgentDefinitionCreator.tsx'
import {WorktreesPanel} from '/src/components/overlays/WorktreesPanel.tsx'
import {useAppStore} from '/src/store/app.ts'
import {ResourceError} from '/src/lib/resourceError.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',cwd:'/fixture',title:'Original 原文'},{id:'other',cwd:'/other',title:'Other 原文'}]})
window.other=()=>useAppStore.setState({activeSessionId:'other'})
const view=new URLSearchParams(location.search).get('view')
createRoot(document.getElementById('root')).render(view.startsWith('agent')?React.createElement(AgentDefinitionCreator,{sessionId:'fixture',seed:{name:'writer',display_name:'User 原文',description:'Description 原文',agent_type:'agent',system_prompt:'Prompt 原文',edit_revision:'revision',edit_scope:'user'},agents:[],refresh:async()=>{throw new ResourceError({key:'ui.http.invalidResponse'})}}):React.createElement(WorktreesPanel))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['agent-error','agent-saved','worktree-load','worktree-partial','worktree-scope']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  let restoreRoute, restoreEntered;const entered=new Promise(resolve=>{restoreEntered=resolve});const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),path=new URL(request.url()).pathname;requests.push({method:request.method(),path})
   const broken=()=>route.fulfill({status:200,body:'broken',contentType:'application/json'})
   if(path.endsWith('/agents/writer'))return view==='agent-error'?broken():route.fulfill({json:{updated:true,agent:{name:'writer',agent_type:'agent'},revision:'b'.repeat(64),refresh_errors:[{session_id:'fixture',code:'provider/error',message:'Provider 原文'},{session_id:'other',code:'provider/error',message:'Second 原文'}]}})
   if(path.endsWith('/personalities'))return route.fulfill({json:{profiles:[]}})
   if(path.endsWith('/skills'))return route.fulfill({json:{skills:[],inherited_skills:[]}})
   if(view==='worktree-load')return broken()
   if(path.endsWith('/restore')){if(view==='worktree-scope'){restoreRoute=route;restoreEntered();return}return route.fulfill({json:{...archived,status:'active'}})}
   if(path.endsWith('/worktrees'))return route.fulfill({json:{worktrees:[archived]}})
   if(path.endsWith('/git'))return route.fulfill({json:repository})
   return broken()
  })
  await page.goto(server.resolvedUrls.local[0]+'__definition-worktree-feedback?view='+view)
  if(view.startsWith('agent')){
   await page.getByRole('dialog').waitFor()
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.agent.author.editSave')),exact:true}).click()
  }else if(view!=='worktree-load'){
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.worktrees.restoreAndOpen')),exact:true}).click()
   if(view==='worktree-scope'){
    await entered
    const loaded=page.waitForResponse(response=>response.url().includes('/sessions/other/git'))
    await page.evaluate(()=>window.other())
    await loaded
    await restoreRoute.fulfill({json:{...archived,path:'/stale-original',status:'active'}})
    await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.worktrees.restoreAndOpen')),exact:true}).waitFor()
    assert.equal(await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.worktrees.open')),exact:true}).count(),0)
    assert.equal(requests.filter(request=>request.method==='POST'&&request.path.endsWith('/sessions')).length,0)
    await page.close();console.log('worktree-scope: late original restore cannot replace current rows or open a conversation');continue
   }
  }
  if(view==='agent-saved')await page.waitForFunction(()=>!document.querySelector('[role=dialog]'))
  else await page.getByRole('alert').first().waitFor()
  const writes=requests.filter(request=>request.method!=='GET').length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   const expected=await page.evaluate(view=>view==='agent-saved'?window.copy('ui.agent.author.savedRefreshFailed',{reason:window.copy('ui.http.invalidResponse')}):view==='worktree-partial'?window.copy('ui.worktrees.restorePartial',{reason:window.copy('ui.http.invalidResponse')}):window.copy('ui.http.invalidResponse'),view)
   await page.waitForFunction(expected=>document.body.textContent.includes(expected),expected)
   assert.equal(requests.filter(request=>request.method!=='GET').length,writes,'locale must not repeat writes')
   if(view==='agent-saved'){
    const separator=await page.evaluate(()=>window.copy('ui.common.feedbackPair',{first:'Provider 原文',second:'Second 原文'}))
    assert.ok((await page.locator('body').textContent()).includes(separator))
   }
  }
  assert.deepEqual(errors,[]);await page.close();console.log(view+': resource feedback follows locale and writes retain ownership')
 }
},{entry:'/__definition-worktree-feedback-entry.js',vite:{plugins:[{
 name:'definition-worktree-feedback-fixture',resolveId(id){if(id==='/__definition-worktree-feedback-entry.js')return id},load(id){if(id==='/__definition-worktree-feedback-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__definition-worktree-feedback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__definition-worktree-feedback','<html><body><div id="root"></div><script type="module" src="/__definition-worktree-feedback-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
