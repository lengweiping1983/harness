import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Inspector} from '/src/components/inspector/Inspector.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useChatStore,emptySession} from '/src/store/chat.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en'); window.locale=setLocale; window.reads=[]
window.fetch=async(path,options={})=>{
 window.reads.push({path:String(path),method:options.method||'GET'})
 if(String(path).endsWith('/plans'))return Response.json({plans:[]})
 throw Error('Unexpected checklist request: '+path)
}
useAppStore.setState({activeSessionId:'fixture',inspectorTab:'todos'})
useChatStore.setState({sessions:{fixture:{...emptySession(),todos:[]}}})
window.todos=items=>useChatStore.setState(state=>({sessions:{...state.sessions,fixture:{...state.sessions.fixture,todos:items}}}))
createRoot(document.getElementById('root')).render(React.createElement('main',{className:'h-screen w-full max-w-2xl bg-surface text-ink'},React.createElement(Inspector)))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage({viewport:{width:390,height:900}}), errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__checklist')
 const panel=page.locator('#inspector-tab-content')
 await panel.getByText('No task checklist yet',{exact:true}).waitFor()
 await panel.getByText('Plans are saved here. You can revisit approved versions and feedback.',{exact:true}).waitFor()
 const matrix=async check=>{
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   await check(locale)
   assert.equal(await panel.evaluate(node=>node.scrollWidth<=node.clientWidth+1),true,'Checklist must fit without horizontal scrolling')
  }
 }
  const original = 'Original checklist 原文 /' + 'long-path-'.repeat(40)
  await page.evaluate(original=>window.todos([
    {id:'done',content:original,status:'completed'},
    {id:'running',content:'Working 原文',status:'in_progress'},
    {id:'waiting',content:'Waiting 原文',status:'pending'},
    {id:'unknown',content:'Unknown 原文',status:'constructor'},
  ]),original)
  await matrix(async locale=>{
    const progress=panel.getByRole('progressbar',{name:locale==='en'?'Task list':'执行清单'})
    await progress.waitFor()
    assert.equal(await progress.getAttribute('aria-valuenow'),'1')
    assert.equal(await progress.getAttribute('aria-valuemax'),'4')
    assert.equal(await progress.getAttribute('aria-valuetext'),locale==='en'?'1/4 completed':'已完成 1/4 项')
    for(const status of locale==='en'?['Completed','In progress','Not started','Unknown']:['已完成','进行中','未开始','状态未知']) await panel.getByText(status,{exact:true}).waitFor()
    await panel.getByText(original,{exact:true}).waitFor()
    assert.equal(await panel.locator('li svg').count(),4,'Every status needs an icon as well as a text label')
    if(locale==='zh-CN' && process.env.HARNESS_TEST_CHECKLIST_SCREENSHOT) await panel.screenshot({path:process.env.HARNESS_TEST_CHECKLIST_SCREENSHOT})
  })
  await page.evaluate(()=>window.todos([{id:'done',content:'Finished 原文',status:'completed'}]))
  await panel.getByRole('progressbar').waitFor()
  assert.equal(await panel.getByRole('progressbar').getAttribute('aria-valuetext'),'1/1 completed')
  await page.evaluate(()=>window.todos([]))
  await matrix(async locale=>{
    await panel.getByText(locale==='en'?'No task checklist yet':'还没有执行清单',{exact:true}).waitFor()
    assert.equal(await panel.getByRole('progressbar').count(),0,'An empty checklist must not invent zero progress')
  })
  assert.equal(await page.evaluate(()=>window.reads.length),1,'Checklist presentation must not reload plans or write progress')

 assert.equal(await page.evaluate(()=>window.reads.some(row=>row.method!=='GET')),false,'Viewing progress must not write task state')
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Inspector checklist: explicit status, accessible progress, long paths, empty/completed/unknown states, en→zh→en and theme/width matrix passed')
},{entry:'/__checklist-entry.js',vite:{plugins:[{
 name:'checklist-fixture',resolveId(id){if(id==='/__checklist-entry.js')return id},load(id){if(id==='/__checklist-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__checklist',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__checklist','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__checklist-entry.js"></script></body></html>'))}catch(error){next(error)}})},
}]}})
