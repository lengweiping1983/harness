import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Composer} from '/src/components/chat/Composer.tsx'
import {useAppStore} from '/src/store/app.ts'
import {usePlanModeStore} from '/src/store/planModes.ts'
import {useStopStore} from '/src/store/stops.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',resolved_cwd:'/fixture'}]})
window.locale=setLocale
window.label=key=>t(key)
window.target=value=>usePlanModeStore.setState({saves:{fixture:{pending:false,target:value,message:{key:'ui.http.invalidResponse'}}}})
window.pending=value=>usePlanModeStore.setState({saves:{fixture:{pending:true,target:value}}})
window.target(true);useStopStore.setState({requests:{fixture:{turn:1,phase:'unconfirmed',message:{key:'ui.store.stops.5ad9d939a628'}}}});setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(Composer,{running:true,queued:3,planMode:false,connection:'open',onSend:()=>false,onCancel:()=>{},onTogglePlan:()=>{}}))
`
const html='<html><body><div id="root"></div><script type="module" src="/__composer-feedback-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v1/connection',route=>route.fulfill({json:{version:2,revision:'fixture',providers:[],models:[],current:null,cycle:1}}))
 await page.route('**/api/v1/sessions/fixture/queue',route=>route.fulfill({json:{runs:[]}}))
 await page.route('**/api/v1/sessions/fixture/permissions',route=>route.fulfill({json:{permissions:{session_id:'fixture',permission_policy:'request_approval',default_policy:'request_approval',scope:'fixture',inherited:false,version:'a'.repeat(64)}}}))
 await page.route('**/api/v1/screenshot/capabilities',route=>route.fulfill({json:{native:false,hideWindow:false,reason:''}}))
 let source='git',more=false,truncated=false,reads=0
 await page.route('**/api/v1/sessions/fixture/files/search?*',route=>{
  reads++;const query=new URL(route.request().url()).searchParams.get('q')
  return route.fulfill({json:{session_id:'fixture',query,root:'/fixture',source,has_more:more,truncated,matches:[{kind:'file',path:query+'.txt',absolute_path:'/fixture/'+query+'.txt'}]}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__composer-feedback')
 const input=page.locator('textarea');await input.waitFor()
 for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({language,theme})=>{window.locale(language);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{language,theme})
  for(const key of ['ui.http.invalidResponse','ui.store.stops.5ad9d939a628'])await page.getByText(await page.evaluate(key=>window.label(key),key),{exact:true}).waitFor()
  for(const target of [true,false]){
   await page.evaluate(target=>window.target(target),target)
   const retry=language==='en'?(target?'Retry entering plan mode':'Retry exiting plan mode'):(target?'重试进入计划模式':'重试退出计划模式')
   await page.getByRole('button',{name:retry,exact:true}).waitFor()
  }
  for(const target of [true,false]){
   await page.evaluate(target=>window.pending(target),target)
   const key=target?'ui.composerFeedback.enterPending':'ui.composerFeedback.exitPending'
   const expected=language==='en'?(target?'Requesting entry into plan mode…':'Requesting exit from plan mode…'):(target?'正在请求进入计划模式…':'正在请求退出计划模式…')
   assert.equal(await page.evaluate(key=>window.label(key),key),expected)
   await page.getByText(expected,{exact:true}).waitFor()
  }
  await page.evaluate(()=>window.target(false))
  await page.getByText(language==='en'?'Queued: 3':'排队：3',{exact:true}).waitFor()
 }
 for(source of ['git','filesystem'])for(more of [false,true])for(truncated of [false,true]){
  const query='q'+reads
  await input.fill('@'+query);await page.getByRole('option',{name:/^q\d+\.txt/}).waitFor()
  const before=reads
  for(const language of ['en','zh-CN','en']){
   await page.evaluate(language=>window.locale(language),language)
   const base=language==='en'?(source==='git'?'Project files · Follows Git ignore rules':'Project files · Skips dependency and cache directories'):(source==='git'?'项目文件 · 遵循 Git 忽略规则':'项目文件 · 跳过依赖与缓存目录')
   const expected=base+(more?(language==='en'?' · Continue typing to narrow the scope':' · 继续输入可缩小范围'):'')+(truncated?(language==='en'?' · The file list is incomplete':' · 文件列表不完整'):'')
   await page.getByText(expected,{exact:true}).waitFor()
  }
  assert.equal(reads,before,'Language switches must not repeat a directory query')
 }
 assert.deepEqual(errors,[])
 console.log('Composer feedback: complete plan retry/queue phrases and all eight directory status combinations across repeated bilingual switches passed')
},{entry:'/__composer-feedback-entry.js',vite:{plugins:[{name:'composer-feedback-fixture',resolveId(id){if(id==='/__composer-feedback-entry.js')return id},load(id){if(id==='/__composer-feedback-entry.js')return entry},configureServer(server){server.middlewares.use('/__composer-feedback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__composer-feedback',html))}catch(error){next(error)}})}}]}})
