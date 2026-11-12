import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {TaskQueue} from '/src/components/inspector/TaskQueue.tsx'
import {useAppStore} from '/src/store/app.ts'
import {responseIssue} from '/src/lib/responseValidation.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.reads=[];window.calls=[];window.hidden=false
Object.defineProperty(document,'hidden',{get:()=>window.hidden,configurable:true})
window.visibility=value=>{window.hidden=value;document.dispatchEvent(new Event('visibilitychange'))}
window.fetch=async(path,options={})=>{
 window.calls.push({path:String(path),method:options.method||'GET'})
 if(String(path).endsWith('/queue'))return Response.json({runs:[]})
 if(String(path).includes('/runs?'))return Response.json({runs:[],has_more:false,next_before:null})
 return new Promise(resolve=>window.reads.push({path:String(path),signal:options.signal,resolve}))
}
window.answer=async(index,kind='valid',text='Original task 原文')=>{
 const row=window.reads[index],parts=row.path.split('/'),owner=decodeURIComponent(parts[4]),id=parts.at(-2)==='runs'?decodeURIComponent(parts.at(-1)):'run-for-turn'
 const response=kind==='json'?new Response('not json',{status:200}):kind==='external'?Response.json({message:'Original external 原文 {reason}'},{status:503}):kind==='missing'?Response.json({code:'run/turn-not-found',message:'Original missing 原文'},{status:404}):kind==='ambiguous'?Response.json({code:'run/ambiguous-turn',message:'Original ambiguous 原文'},{status:409}):Response.json({id:kind==='wrong-id'?'different-run':id,session_id:kind==='foreign'?'other':owner,text,status:kind==='running'?'running':'completed',created_at:1700000000,...(kind==='schema'?{text:42}:{})})
 row.body=await response.clone().text()
 if(response.ok&&kind!=='json')row.issue=responseIssue(row.path,'GET',200,JSON.parse(row.body))
 row.resolve(response)
}
let seq=0
window.select=(owner,runId='first')=>useAppStore.setState({activeSessionId:owner,inspectorRunTarget:{sessionId:owner,runId,seq:++seq}})
window.turn=turn=>useAppStore.setState({inspectorRunTarget:{sessionId:useAppStore.getState().activeSessionId,turn,seq:++seq}})
window.clone=()=>useAppStore.setState(s=>({inspectorRunTarget:{...s.inspectorRunTarget}}))
window.select('owner')
function Fixture(){const owner=useAppStore(s=>s.activeSessionId),[active,setActive]=useState(false);window.active=setActive;return <main className="mx-auto max-w-xl space-y-3 p-2"><input aria-label="Original draft" defaultValue="Keep 用户草稿"/><TaskQueue key={owner} sessionId={owner} active={active}/></main>}
createRoot(document.getElementById('root')).render(<Fixture/>)
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 await page.goto(server.resolvedUrls.local[0]+'__run-preview-lifecycle')
 const preview=page.locator('section').filter({has:page.locator('h3[data-panel-initial-focus]')})
 const copy=key=>page.evaluate(key=>window.copy(key),key)
 const reads=()=>page.evaluate(()=>window.reads.length)
 const waitReads=count=>page.waitForFunction(count=>window.reads.length===count,count)
 const answer=(index,kind='valid',text)=>page.evaluate(({index,kind,text})=>window.answer(index,kind,text),{index,kind,text})
 const retry=async()=>preview.getByRole('button',{name:await copy('ui.components.inspector.RunPreview.ffbc7aa037bd'),exact:true})
 await preview.getByText(await copy('ui.runPreview.readPaused'),{exact:true}).waitFor()
 assert.equal(await reads(),0)
 assert.equal(await page.locator('h3[data-panel-initial-focus]').evaluate(node=>node===document.activeElement),false)
 await page.evaluate(()=>window.active(true))
 await waitReads(1)

 // 面板隐藏和页面隐藏都取消原 GET；迟到成功与失败不能归还到新读取。
 await page.evaluate(()=>window.active(false))
 await page.waitForFunction(()=>window.reads[0].signal.aborted)
 await page.clock.runFor(5000);assert.equal(await reads(),1)
 await answer(0,'valid','STALE HIDDEN SUCCESS')
 await page.evaluate(()=>window.active(true));await waitReads(2)
 await page.evaluate(()=>window.visibility(true));await page.waitForFunction(()=>window.reads[1].signal.aborted)
 await answer(1,'external')
 const draft=page.getByRole('textbox',{name:'Original draft'});await draft.focus()
 await page.clock.runFor(5000);assert.equal(await reads(),2)
 await page.evaluate(()=>window.visibility(false));await waitReads(3)
 assert.equal(await draft.evaluate(node=>node===document.activeElement),true,'Resuming observation must not steal editor focus')
 await answer(2,'json');await preview.getByRole('alert').waitFor()
 assert.equal(await page.getByText('STALE HIDDEN SUCCESS',{exact:true}).count(),0)
 assert.equal(await page.getByText('Original external 原文 {reason}',{exact:true}).count(),0)

 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440])for(const font of [10,22]){
  await page.setViewportSize({width,height:1100});await page.evaluate(({locale,dark,font})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark);document.documentElement.style.fontSize=font+'px'},{locale,dark,font})
  await preview.getByText(await copy('ui.http.invalidResponse'),{exact:true}).waitFor()
  await (await retry()).waitFor();assert.equal(await reads(),3)
  assert.equal(await draft.inputValue(),'Keep 用户草稿')
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }
 if(process.env.HARNESS_TEST_SCREENSHOT)await page.screenshot({path:process.env.HARNESS_TEST_SCREENSHOT})
 await (await retry()).focus();await page.keyboard.press('Enter');await waitReads(4)
 await answer(3,'wrong-id')
 try{await preview.getByText(await copy('ui.lib.runReveal.1b916fec6f48'),{exact:true}).waitFor()}
 catch(error){throw new Error('Run preview retry state: '+JSON.stringify({text:await preview.innerText(),reads:await page.evaluate(()=>window.reads.map(row=>({path:row.path,aborted:row.signal.aborted,body:row.body,issue:row.issue}))),errors}),{cause:error})}
 for(const locale of ['zh-CN','en']){await page.evaluate(locale=>window.locale(locale),locale);await preview.getByText(await copy('ui.lib.runReveal.1b916fec6f48'),{exact:true}).waitFor();assert.equal(await reads(),4)}
 await page.evaluate(()=>window.clone());assert.equal(await reads(),4,'An equivalent target object must not restart observation')

 // 按轮次读取同样经过生成契约；外部诊断保持原文，404与歧义保留不可用含义。
 for(const kind of ['schema','external','missing','ambiguous']){
  const before=await reads();await page.evaluate(()=>window.turn(2));await waitReads(before+1)
  assert.ok((await page.evaluate(()=>window.reads.at(-1).path)).endsWith('/turns/2/run'))
  await answer(before,kind);await preview.getByRole('alert').waitFor()
  for(const locale of ['en','zh-CN','en']){
   await page.evaluate(locale=>window.locale(locale),locale)
   await preview.getByRole('heading',{name:locale==='en'?'Turn 2 execution task':'第 2 轮次执行任务'}).waitFor()
   await preview.getByRole('button',{name:locale==='en'?'Close':'关闭',exact:true}).waitFor()
   await preview.getByText(kind==='schema'?await copy('ui.http.invalidResponse'):kind==='external'?'Original external 原文 {reason}':await copy('ui.collaboration.error.run/'+(kind==='missing'?'turn-not-found':'ambiguous-turn')),{exact:true}).waitFor()
   if(kind==='missing'||kind==='ambiguous')await preview.getByText(await copy('ui.components.inspector.RunPreview.1f6e8bb2b8b9'),{exact:true}).waitFor()
   assert.equal(await reads(),before+1)
  }
 }
 const beforeInvalid=await reads();await page.evaluate(()=>window.turn(0))
 await preview.getByText(await copy('ui.lib.runReveal.3b7cb0f78970'),{exact:true}).waitFor()
 for(const locale of ['zh-CN','en']){await page.evaluate(locale=>window.locale(locale),locale);await preview.getByText(await copy('ui.lib.runReveal.3b7cb0f78970'),{exact:true}).waitFor()}
 assert.equal(await reads(),beforeInvalid)

 // 对话及目标 A→B→A 分别完成渲染后，旧回包不能替换当前记录。
 const beforeSwitch=await reads()
 await page.evaluate(()=>window.select('owner','old'));await waitReads(beforeSwitch+1)
 await page.evaluate(()=>window.select('other','other'));await waitReads(beforeSwitch+2)
 await page.evaluate(()=>window.select('owner','current'));await waitReads(beforeSwitch+3)
 assert.equal(await page.evaluate(index=>window.reads.slice(index,index+2).every(row=>row.signal.aborted),beforeSwitch),true)
 await answer(beforeSwitch+2,'valid','CURRENT CONFIRMED RECORD');await preview.getByText('CURRENT CONFIRMED RECORD',{exact:true}).last().waitFor()
 await answer(beforeSwitch,'valid','STALE OLD RECORD');await answer(beforeSwitch+1,'external')
 await page.evaluate(()=>window.clone());await preview.getByText('CURRENT CONFIRMED RECORD',{exact:true}).last().waitFor()
 assert.equal(await page.getByText('STALE OLD RECORD',{exact:true}).count(),0)

 // 运行中记录会读取展开详情；这次嵌套读取也必须随面板隐藏取消。
 const beforeNested=await reads();await page.evaluate(()=>window.select('owner','live'));await waitReads(beforeNested+1)
 await answer(beforeNested,'running');await waitReads(beforeNested+2)
 await page.evaluate(()=>window.active(false));await page.waitForFunction(index=>window.reads[index].signal.aborted,beforeNested+1)
 await answer(beforeNested+1,'valid','STALE NESTED RECORD')
 await page.evaluate(()=>window.active(true));await waitReads(beforeNested+3)
 await answer(beforeNested+2,'running');await waitReads(beforeNested+4)
 await answer(beforeNested+3,'foreign');await preview.getByRole('alert').getByText(await copy('ui.http.invalidResponse'),{exact:true}).waitFor()
 assert.equal(await page.getByText('STALE NESTED RECORD',{exact:true}).count(),0)

 const beforeClose=await reads();await page.evaluate(()=>window.select('owner','close'));await waitReads(beforeClose+1)
 await preview.getByRole('button',{name:await copy('ui.components.inspector.RunPreview.e6a2caa875a5'),exact:true}).click()
 await preview.waitFor({state:'detached'});assert.equal(await page.evaluate(index=>window.reads[index].signal.aborted,beforeClose),true)
 await answer(beforeClose,'valid','STALE CLOSED RECORD')
 assert.equal(await page.getByText('STALE CLOSED RECORD',{exact:true}).count(),0)
 assert.equal(await draft.inputValue(),'Keep 用户草稿')
 assert.equal(await page.evaluate(()=>window.calls.some(row=>row.method!=='GET')),false,'Inspection and retry cannot submit or continue a run')
 assert.deepEqual(errors,[]);await page.close()
 console.log('Run preview: generated turn contract, resource errors and external originals, visible read ownership, nested details, retry/close, precise targets, draft/focus and en→zh→en theme/width/font matrix passed with GET only')
},{entry:'/__run-preview-lifecycle-entry.jsx',vite:{plugins:[{
 name:'run-preview-lifecycle-fixture',resolveId(id){if(id==='/__run-preview-lifecycle-entry.jsx')return id},load(id){if(id==='/__run-preview-lifecycle-entry.jsx')return entry},
 configureServer(server){server.middlewares.use('/__run-preview-lifecycle',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__run-preview-lifecycle','<html><body><div id="root"></div><script type="module" src="/__run-preview-lifecycle-entry.jsx"></script></body></html>'))}catch(error){next(error)}})},
}]}})
