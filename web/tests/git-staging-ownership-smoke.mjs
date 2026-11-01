import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const head='a'.repeat(40),oldHead='b'.repeat(40)
const repository={root:'/fixture',branch:'main',refs:['main'],head:oldHead,index_revision:'initial',conflicts:false,files:[{path:'a.txt',status:'M '}],staged_files:[{path:'a.txt',status:'M'}]}
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {GitStaging} from '/src/components/overlays/GitStaging.tsx'
import {useGitDraftStore,gitDraftScope} from '/src/store/gitDrafts.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
const repository=${JSON.stringify(repository)},scope=gitDraftScope('owner','/fixture')
window.copy=t;window.locale=setLocale;window.updates=[];window.busy=[]
useGitDraftStore.getState().setMessage(scope,'Original commit 原文')
window.draft=()=>useGitDraftStore.getState().byScope[scope]??''
window.writeDraft=value=>useGitDraftStore.getState().setMessage(scope,value)
const root=createRoot(document.getElementById('root'));let serial=0
function Fixture(){const [repo,setRepo]=useState(repository),[busy,setBusy]=useState(false);return React.createElement(GitStaging,{sessionId:'owner',repository:repo,busy,onBusy:value=>{window.busy.push(value);setBusy(value)},onUpdate:value=>{window.updates.push(value);setRepo(value)}})}
window.show=()=>root.render(React.createElement(Fixture,{key:++serial}));window.hide=()=>root.render(null);window.show()
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['commit','commit-new-draft','commit-same-draft','stage','unstage','unknown','foreign','readback-failed','reopen','closed','patch','refresh']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  let release,entered,writes=0,reads=0
  const held=new Promise(resolve=>entered=resolve),done=new Promise(resolve=>release=resolve)
  const saved={...repository,head,index_revision:'saved',files:[],staged_files:[]}
  await page.route('**/api/v1/**',async route=>{
   const request=route.request(),url=new URL(request.url())
   if(request.method()==='POST'){
    writes++;entered();await done
    if(mode==='unknown'||mode==='readback-failed')return route.fulfill({status:201,body:'broken',contentType:'application/json'})
    const value=url.pathname.endsWith('/commit')?{commit:head,repository:mode==='foreign'?{...saved,root:'/other'}:saved}:saved
    return route.fulfill({json:value})
   }
   reads++
   if(url.pathname.endsWith('/staged'))return route.fulfill({json:{path:'other.txt',diff:'Original diff',binary:false,truncated:false}})
   if(mode==='readback-failed')return route.fulfill({status:503,json:{message:'Original readback failure 原文'}})
   return route.fulfill({json:mode==='refresh'?{...saved,root:'/other'}:saved})
  })
  await page.goto(server.resolvedUrls.local[0]+'__git-staging-ownership')
  await page.getByRole('textbox',{name:await page.evaluate(()=>window.copy('ui.git.message')),exact:true}).waitFor()
  const button=async(key,params)=>page.getByRole('button',{name:await page.evaluate(({key,params})=>window.copy(key,params),{key,params}),exact:true})
  if(mode==='patch'||mode==='refresh'){
   await (await button(mode==='patch'?'ui.git.inspect':'ui.worktrees.refresh',mode==='patch'?{path:'a.txt'}:undefined)).click()
   await page.getByText(await page.evaluate(()=>window.copy('ui.http.invalidResponse')),{exact:true}).waitFor()
   assert.equal(writes,0);assert.equal(reads,1);assert.equal(await page.evaluate(()=>window.updates.length),0)
  }else{
   if(mode==='unstage')await page.getByRole('checkbox').click()
   const action=await button(mode==='stage'?'ui.git.stageAll':mode==='unstage'?'ui.git.unstageSelected':'ui.git.commit',mode==='unstage'?{count:1}:undefined)
   await action.evaluate(element=>{const props=element[Object.keys(element).find(key=>key.startsWith('__reactProps$'))];props.onClick();props.onClick()})
   await held;assert.equal(writes,1)
   if(mode==='commit-new-draft')await page.evaluate(()=>window.writeDraft('New draft 原文'))
   if(mode==='commit-same-draft')await page.evaluate(()=>{window.writeDraft('Intermediate');window.writeDraft('Original commit 原文')})
   if(mode==='reopen'||mode==='closed'){
    await page.evaluate(()=>window.hide());await page.getByRole('textbox').waitFor({state:'detached'})
    await page.evaluate(()=>window.writeDraft('New draft 原文'))
    if(mode==='reopen'){
     await page.evaluate(()=>window.show());await page.getByRole('textbox').waitFor()
     await (await button('ui.git.commit')).click()
    }
   }
   release()
   if(mode==='closed'){
    await page.waitForFunction(()=>window.busy.at(-1)===false)
    assert.equal(await page.evaluate(()=>window.updates.length),0)
   }else{
    await page.waitForFunction(()=>window.updates.length===1||document.querySelector('[role="alert"]'))
    await page.waitForFunction(()=>window.busy.at(-1)===false)
   }
   const expected=mode==='commit'?'':mode==='commit-new-draft'||mode==='reopen'||mode==='closed'?'New draft 原文':'Original commit 原文'
   assert.equal(await page.evaluate(()=>window.draft()),expected)
   assert.equal(writes,1)
   const unknown=['unknown','foreign','readback-failed'].includes(mode)
   assert.equal(reads,unknown?1:0)
   if(unknown){
    for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
     await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
     const alert=page.getByRole('alert');await alert.waitFor()
     assert.ok((await alert.innerText()).includes(locale==='zh-CN'?'Git 操作可能已完成':'The Git operation may have completed'))
     assert.equal(writes,1);assert.equal(reads,1)
    }
   }
  }
  assert.deepEqual(errors,[]);await page.close()
  console.log('Git staging ownership: '+mode)
 }
},{entry:'/__git-staging-ownership-entry.js',vite:{plugins:[{
 name:'git-staging-ownership-fixture',resolveId(id){if(id==='/__git-staging-ownership-entry.js')return id},load(id){if(id==='/__git-staging-ownership-entry.js')return entry},configureServer(server){server.middlewares.use('/__git-staging-ownership',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__git-staging-ownership','<html><body><div id="root"></div><script type="module" src="/__git-staging-ownership-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
