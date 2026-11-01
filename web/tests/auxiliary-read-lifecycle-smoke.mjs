import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {DirectoryField} from '/src/components/ui/DirectoryField.tsx'
import {CommandPalette} from '/src/components/overlays/CommandPalette.tsx'
import {MemoryPanel} from '/src/components/overlays/MemoryPanel.tsx'
import {ChatGPTLogin} from '/src/components/overlays/ChatGPTLogin.tsx'
import {WorktreeReview} from '/src/components/overlays/WorktreeReview.tsx'
import {WorktreeEnvironment} from '/src/components/overlays/WorktreeEnvironment.tsx'
import {GitStaging} from '/src/components/overlays/GitStaging.tsx'
import {InvitationPage} from '/src/components/shared/InvitationPage.tsx'
import {SharedTaskInput} from '/src/components/shared/SharedTaskInput.tsx'
import {collaboration} from '/src/lib/collaboration.ts'
import {saveInvitationRecovery} from '/src/lib/invitationRecovery.ts'
import {useAppStore} from '/src/store/app.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
const root=createRoot(document.getElementById('root'));window.reads=[];window.copy=t;window.hidden=false
Object.defineProperty(document,'hidden',{get:()=>window.hidden,configurable:true})
window.visibility=value=>{window.hidden=value;document.dispatchEvent(new Event('visibilitychange'))}
window.fetch=(path,options={})=>new Promise(resolve=>window.reads.push({path:String(path),method:options.method??'GET',signal:options.signal,resolve}))
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',title:'Fixture',cwd:'/fixture'}],workspace:{root:'/fixture'},activeWorkspaceId:'/fixture',workspaces:[{id:'/fixture',path:'/fixture',name:'Fixture'}]})
const noop=()=>{},repository={root:'/fixture',branch:'main',refs:[],head:'head',index_revision:'revision',conflicts:false,staged_files:[{path:'a.ts',status:'M'}],files:[{path:'a.ts',status:'M'}]}
const scope=JSON.stringify(['shared','fixture','guest'])
useDraftStore.getState().setText(scope,'Original draft')
useDraftStore.getState().rememberSubmission(scope,{id:'original',text:'Original draft',attachments:[],action:'send',status:'unknown'})
collaboration.state=async()=>({ok:true,data:{access:{members:{guest:['read','contribute']}}}})
collaboration.queue=async()=>({ok:true,data:{runs:[]}})
saveInvitationRecovery('hi1.fixture.secret','a'.repeat(43))
window.show=(kind,active,language)=>{
 setLocale(language);useAppStore.setState({commandOpen:active})
 const element=kind==='directory'?React.createElement(DirectoryField,{value:'/fixture',label:'Fixture directory',onChange:noop}):kind==='command'?React.createElement(CommandPalette):kind==='memory'?React.createElement(MemoryPanel):kind==='review'?React.createElement(WorktreeReview,{id:'tree',defaultOpen:true,active}):kind==='environment'?React.createElement(WorktreeEnvironment,{id:'tree',sessionId:'fixture',defaultOpen:true,active}):kind==='git'?React.createElement(GitStaging,{sessionId:'fixture',repository,busy:false,onBusy:noop,onUpdate:noop,active}):kind==='login'?React.createElement(ChatGPTLogin,{onStatus:noop}):kind==='invitation'?React.createElement(InvitationPage,{token:'hi1.fixture.secret'}):React.createElement(SharedTaskInput,{sessionId:'fixture',subject:'guest',initialContribute:true})
 root.render(!active&&['memory','directory'].includes(kind)?null:element)
}
window.finish=index=>{window.reads[index].settled=true;window.reads[index].resolve(new Response(JSON.stringify({error:'STALE READ'}),{status:503,headers:{'Content-Type':'application/json'}}))}
`
await browserScenario(async({server,browser})=>{
 for(const kind of ['directory','command','memory','review','environment','git','login','invitation','receipt']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  await page.goto(server.resolvedUrls.local[0]+'__auxiliary-reads');await page.waitForFunction(()=>typeof window.show==='function')
  await page.evaluate(kind=>window.show(kind,true,'en'),kind)
  if(kind==='directory')await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.directory.browseFor',{label:'Fixture directory'})),exact:true}).click()
  if(kind==='git')await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.git.inspect',{path:'a.ts'})),exact:true}).click()
  if(kind==='memory')await page.clock.runFor(32)
  await page.waitForFunction(()=>window.reads.length>0)
  if(['login','invitation','receipt'].includes(kind)){
   if(kind==='login')await page.evaluate(()=>{for(let i=0;i<5;i++)window.dispatchEvent(new Event('chatgpt-account-changed'))})
   await page.clock.runFor(15000);assert.equal(await page.evaluate(()=>window.reads.length),1,kind+' slow reads must not overlap')
   await page.evaluate(()=>window.visibility(true))
  }else await page.evaluate(kind=>window.show(kind,false,'en'),kind)
  await page.waitForFunction(()=>window.reads.every(r=>r.signal?.aborted))
  const count=await page.evaluate(()=>window.reads.length)
  await page.evaluate(()=>window.reads.forEach((_r,index)=>window.finish(index)))
  await page.clock.runFor(32);assert.equal(await page.getByText('STALE READ',{exact:true}).count(),0)
  if(['login','invitation','receipt'].includes(kind))await page.evaluate(()=>window.visibility(false))
  else await page.evaluate(kind=>window.show(kind,true,'zh-CN'),kind)
  if(kind==='directory')await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.directory.browseFor',{label:'Fixture directory'})),exact:true}).click()
  if(kind==='git')await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.git.inspect',{path:'a.ts'})),exact:true}).click()
  if(kind==='memory')await page.clock.runFor(32)
  await page.waitForFunction(count=>window.reads.length>count,count)
  assert.deepEqual(errors,[]);await page.close();console.log(kind+': cancelled reads, stale failure isolation and recovery passed')
 }
},{entry:'/__auxiliary-reads-entry.js',vite:{plugins:[{
 name:'auxiliary-reads-fixture',resolveId(id){if(id==='/__auxiliary-reads-entry.js')return id},load(id){if(id==='/__auxiliary-reads-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__auxiliary-reads',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__auxiliary-reads','<html><body><div id="root"></div><script type="module" src="/__auxiliary-reads-entry.js"></script></body></html>'))}catch(e){next(e)}})}
}]}})
