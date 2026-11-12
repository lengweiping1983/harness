import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {browserScenario} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'
import {cleanupFixture,withDeadline} from './fixture-cleanup.mjs'
const root=fileURLToPath(new URL('../../',import.meta.url))
const backend=spawn(fixturePython,['-m','tests.web_fixture'],{cwd:root,stdio:['ignore','pipe','pipe']})
const lines=createInterface({input:backend.stdout});let diagnostics='',failure
backend.stderr.on('data',data=>diagnostics+=data)
const entry=`
import {useAppStore} from '/src/store/app.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import {noticeText} from '/src/lib/noticeText.ts'
import {api} from '/src/lib/api.ts'
window.app=useAppStore;window.drafts=useDraftStore;window.locale=setLocale;window.copy=t;window.notice=noticeText
useAppStore.setState({activeSessionId:null,activeWorkspaceId:null,workspaces:[],workspace:null,sessions:[],sessionCreating:false,toasts:[]})
window.ready=true
window.prepareCreation=async()=>{
 const result=await api.listWorkspaces()
 if(!result.ok||result.data.workspaces.length!==1)throw Error('Expected one isolated fixture project')
 const project=result.data.workspaces[0]
 useAppStore.setState({workspaces:[project],activeWorkspaceId:project.id})
 useAppStore.getState().setNewSessionCheckout({cwd:project.path,mode:'local',expected_head:null,expected_branch:null})
}
`
try{
 const ready=await withDeadline(()=>new Promise((resolve,reject)=>{
  backend.once('error',reject);backend.once('exit',code=>reject(Error('Backend exited '+code+' '+diagnostics)))
  lines.on('line',line=>{try{const data=JSON.parse(line);if(Number.isInteger(data.port))resolve(data)}catch{}})
 }),20000,'Creation backend startup')
 const origin='http://127.0.0.1:'+ready.port
 await browserScenario(async({server,browser})=>{
  for(const language of ['en','zh-CN']){
   const context=await browser.newContext(),page=await context.newPage(),errors=[]
   page.on('pageerror',error=>errors.push(error.message))
   let writes=0,hideFirstList=true,created,requestId,creationFailure
   await page.route('**/api/v1/sessions',async route=>{
    if(route.request().method()==='POST'){
     writes++;requestId=route.request().headers()['x-request-id']
     const response=await route.fetch()
     if(response.status()!==200){creationFailure=await response.text();await route.fulfill({response});return}
     created=(await response.json()).session
     await route.abort('connectionreset');return
    }
    if(hideFirstList){hideFirstList=false;await route.fulfill({json:{sessions:[]}});return}
    await route.continue()
   })
   await page.goto(server.resolvedUrls.local[0]+'__creation-recovery')
   await page.waitForFunction(()=>window.ready)
   await page.evaluate(()=>window.prepareCreation())
   assert.equal(await page.evaluate(()=>window.app.getState().materializeSession()),false)
   assert.equal(writes,1);assert.ok(created,creationFailure+'\n'+diagnostics);assert.equal(created.metadata.creation_request_id,requestId);assert.equal(created.metadata.creation_completed,true)
   for(const locale of ['en','zh-CN','en']){
    const translated=await page.evaluate(locale=>{window.locale(locale);const toast=window.app.getState().toasts.at(-1);return [window.notice(toast.description),window.copy('ui.sessionCreation.unconfirmed')]},locale)
    assert.equal(...translated)
   }
   const retained=await page.evaluate(()=>{
    const state=JSON.parse(localStorage.getItem(window.drafts.persist.getOptions().name)).state
    return state.bySession.__unsaved__.creationRequest.id
   })
   assert.equal(retained,requestId)
   await page.reload();await page.waitForFunction(()=>window.ready)
   await page.evaluate(()=>window.prepareCreation())
   assert.equal(await page.evaluate(()=>window.drafts.getState().bySession.__unsaved__.creationRequest.id),requestId)
   await page.evaluate(language=>window.locale(language),language)
   assert.equal(await page.evaluate(()=>window.app.getState().materializeSession()),true)
   assert.equal(writes,1)
   assert.equal(await page.evaluate(()=>window.app.getState().activeSessionId),created.id)
   assert.equal(await page.evaluate(()=>Boolean(window.drafts.getState().bySession.__unsaved__.creationRequest)),false)
   const response=await page.request.get(origin+'/api/v1/sessions'),body=await response.json()
   assert.equal(body.sessions.filter(row=>row.metadata?.creation_request_id===requestId).length,1)
   assert.deepEqual(errors,[]);await context.close()
  }
  console.log('Creation recovery: real backend accepted once, lost response, persisted identity, browser reload and read-only confirmation in English/Chinese')
 },{entry:'/__creation-recovery-entry.js',vite:{server:{proxy:{'/api/v1':{target:origin,changeOrigin:true,headers:{Origin:origin}}}},plugins:[{
  name:'creation-recovery',resolveId(id){if(id==='/__creation-recovery-entry.js')return id},load(id){if(id==='/__creation-recovery-entry.js')return entry},
  configureServer(server){server.middlewares.use('/__creation-recovery',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__creation-recovery','<html><head><link rel="icon" href="data:,"></head><body><script type="module" src="/__creation-recovery-entry.js"></script></body></html>'))}catch(error){next(error)}})},
 }]}})
}catch(error){failure=error}
await cleanupFixture({error:failure,lines,backend})
