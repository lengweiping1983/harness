import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {QuestionCard} from '/src/components/chat/blocks/QuestionCard.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.submissions=[]
const params=new URLSearchParams(location.search),plan=params.get('kind')==='plan',answered=params.get('answered')==='1'
const block={id:'fixture',kind:'question',at:0,questionId:'fixture',answered,questions:[plan?{id:'plan-review',question:'Original plan 原文',options:[{id:'approve',label:'Approve'},{id:'keep-planning',label:'Keep planning'}]}:{id:'q',header:'Original question 原文',question:'Original question 原文'}]}
function App(){const [current,setCurrent]=useState(block);return React.createElement(QuestionCard,{block:current,sessionId:'owner',onAnswer:(id,answers)=>{window.submissions.push({id,answers});setCurrent({...current,answered:true,answers})}})}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
await browserScenario(async({server,browser})=>{
 for(const kind of ['question','plan'])for(const answered of [false,true]){
  const page=await browser.newPage(),errors=[],requests=[];page.setDefaultTimeout(15000)
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
  const key='harness.'+(kind==='plan'?'plan-feedback':'question-draft')+'.v1:'+JSON.stringify(['owner','fixture'])
  await page.addInitScript(({key})=>sessionStorage.setItem(key,'Original damaged input 原文'),{key})
  await page.goto(server.resolvedUrls.local[0]+'__question-draft?kind='+kind+'&answered='+(answered?'1':'0'))
  const checkOriginal=async()=>assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),key),'Original damaged input 原文')
  await page.getByRole('alert').waitFor();await checkOriginal()
  if(!answered){
   if(kind==='plan')await page.locator('summary').getByText(await page.evaluate(()=>window.copy('ui.components.chat.blocks.QuestionCard.6a529601daca')),{exact:true}).click()
   await page.getByRole('textbox').fill('New input 原文')
  }
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   await page.getByRole('alert').getByText(await page.evaluate(()=>window.copy('ui.question.invalidDraft')),{exact:true}).waitFor()
   await checkOriginal();assert.equal(await page.evaluate(()=>window.submissions.length),0)
   if(!answered)assert.equal(await page.getByRole('textbox').inputValue(),'New input 原文')
  }
  if(!answered){
   const submit=await page.evaluate(kind=>window.copy(kind==='plan'?'ui.components.chat.blocks.QuestionCard.4c381122f9a2':'ui.components.chat.blocks.QuestionCard.0c1762dddc4f'),kind)
   await page.getByRole('button',{name:submit,exact:true}).click()
   await page.waitForFunction(()=>window.submissions.length===1)
   await checkOriginal()
   assert.deepEqual(await page.evaluate(()=>window.submissions[0].answers),kind==='plan'?[{id:'plan-review',selected:['Keep planning'],selected_ids:['keep-planning'],custom:'New input 原文'}]:[{id:'q',selected:[],custom:'New input 原文'}])
  }
  assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);await page.close()
  console.log(kind+' '+(answered?'already answered':'new answer')+': corrupt original preserved during mount, edit, locale changes and completion')
 }
},{entry:'/__question-draft-entry.js',vite:{plugins:[{name:'question-draft-fixture',resolveId(id){if(id==='/__question-draft-entry.js')return id},load(id){if(id==='/__question-draft-entry.js')return entry},configureServer(server){server.middlewares.use('/__question-draft',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__question-draft','<html><body><div id="root"></div><script type="module" src="/__question-draft-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
