import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const repository={root:'/fixture',branch:'main',refs:['main'],head:'a'.repeat(40),index_revision:'revision',conflicts:false,staged_files:[{path:'Original 文件.txt',status:'M'}],files:[{path:'Original 文件.txt',status:'M'}]}
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ChangesPanel} from '/src/components/inspector/ChangesPanel.tsx'
import {GitStaging} from '/src/components/overlays/GitStaging.tsx'
import {WorktreeReview} from '/src/components/overlays/WorktreeReview.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
const view=new URLSearchParams(location.search).get('view')
const noop=()=>{}
const component=view==='worktree'?React.createElement(WorktreeReview,{id:'fixture',defaultOpen:true}):view.startsWith('git')?React.createElement(GitStaging,{sessionId:'fixture',repository:${JSON.stringify(repository)},busy:false,onBusy:noop,onUpdate:noop}):React.createElement(ChangesPanel,{sessionId:'fixture',historyTarget:view==='history'?{sessionId:'fixture',turn:1,seq:0}:null,initialHistory:view==='history'})
const root=createRoot(document.getElementById('root'));let generation=0
window.reopen=()=>root.render(React.cloneElement(component,{key:++generation}))
root.render(component)
`
await browserScenario(async({server,browser})=>{
 for(const view of ['non-git','history','list','options','patch','git-error','git-notice','worktree']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  const requests=[],errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const url=new URL(route.request().url());requests.push({method:route.request().method(),url:url.href})
   const invalid=()=>route.fulfill({status:200,body:'not json',contentType:'application/json'})
   if(url.pathname.endsWith('/comparison/options'))return view==='options'?invalid():route.fulfill({json:{branches:['main'],current:'main',upstream:null,commits:[],next_offset:null}})
   if(url.pathname.endsWith('/comparison')){
    if(view==='non-git')return route.fulfill({status:400,json:{error:'Not a Git repository',code:'review/unavailable'}})
    if(view==='list'||url.searchParams.has('path'))return invalid()
    const files=view==='patch'?[{path:'Original 文件.txt',repo_path:'Original 文件.txt',old_path:null,status:'M',added:1,deleted:0}]:[]
    return route.fulfill({json:{available:true,scope:'uncommitted',left:'HEAD',right:'WORKTREE',revision:'revision',workspace:'/fixture',total:files.length,files,next_offset:null,added:files.length,deleted:0}})
   }
   if(url.pathname.endsWith('/git/commit'))return route.fulfill({json:{commit:'b'.repeat(40),repository}})
   return invalid()
  })
  await page.goto(server.resolvedUrls.local[0]+'__review-error?view='+view)
  if(view==='git-error')await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.worktrees.refresh')),exact:true}).click()
  if(view==='git-notice'){
   await page.getByRole('textbox').fill('Commit input 原文')
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.git.commit')),exact:true}).click()
  }
  if(view==='non-git'){
   await page.getByRole('alert').filter({hasText:'Not a Git repository'}).waitFor()
   assert.equal(await page.locator('.changes-panel').getByRole('button',{name:await page.evaluate(()=>window.copy('ui.changes.refresh')),exact:true}).count(),1)
   assert.equal(requests.filter(request=>new URL(request.url).pathname.endsWith('/comparison/options')).length,0)
   await page.evaluate(()=>window.reopen())
   await page.getByRole('alert').filter({hasText:'Not a Git repository'}).waitFor()
   assert.equal(requests.filter(request=>new URL(request.url).pathname.endsWith('/comparison')).length,2)
   assert.equal(requests.filter(request=>new URL(request.url).pathname.endsWith('/comparison/options')).length,0)
   await page.close();console.log('Non-Git comparison never requests Git options, including reopening');continue
  }
  const expected=()=>page.evaluate(view=>window.copy(view==='git-notice'?'ui.git.saved':'ui.http.invalidResponse',view==='git-notice'?{commit:'b'.repeat(12)}:undefined),view)
  await page.getByText(await expected(),{exact:true}).waitFor()
  const before=requests.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await page.getByText(await expected(),{exact:true}).waitFor()
   assert.equal(requests.length,before,view+': language changes must not repeat reads or writes')
  }
  if(view==='git-notice'){assert.equal(requests.filter(request=>request.method==='POST').length,1);assert.equal(await page.getByRole('textbox').inputValue(),'')}
  if(view==='history')assert.equal(requests.filter(request=>new URL(request.url).pathname.includes('/comparison')).length,0,'History must not access Git comparisons or options')
  if(view==='options')assert.equal(await page.getByRole('option',{name:await page.evaluate(()=>window.copy('ui.changes.uncommitted')),exact:true}).getAttribute('title'),await expected())
  assert.deepEqual(errors,[],view)
  await page.close();console.log(view+': saved review feedback follows language without repeating operations')
 }
},{entry:'/__review-error-entry.js',vite:{plugins:[{
 name:'review-error-language-fixture',resolveId(id){if(id==='/__review-error-entry.js')return id},load(id){if(id==='/__review-error-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__review-error',async(_req,res,next)=>{
  try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__review-error','<html><body><div id="root"></div><script type="module" src="/__review-error-entry.js"></script></body></html>'))}catch(error){next(error)}
 })},
}]}})
