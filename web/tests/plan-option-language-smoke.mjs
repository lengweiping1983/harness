import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QuestionCard} from '/src/components/chat/blocks/QuestionCard.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.answers=[]
const labels=['constructor','toString','__proto__','Approve','Keep planning']
const block={id:'plan',kind:'question',at:0,questionId:'plan-options',questions:[{id:'plan-review',question:'Original plan 原文',options:labels.map(label=>({label:label==='Approve'?'Original 批准':label==='Keep planning'?'Original 继续':label,...(label==='Approve'?{id:'approve'}:label==='Keep planning'?{id:'keep-planning'}:{})}))}]}
createRoot(document.getElementById('root')).render(React.createElement(QuestionCard,{block,sessionId:'owner',onAnswer:(id,answers)=>window.answers.push({id,answers})}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__plan-option-language')
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const label of ['constructor','toString','__proto__'])await page.getByRole('button',{name:label,exact:true}).waitFor()
  for(const key of ['b168fdafe0f4','b367cdfb55b5'])await page.getByRole('button',{name:await page.evaluate(key=>window.copy('ui.components.chat.blocks.QuestionCard.'+key),key),exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>window.answers.length),0)
 }
 await page.getByRole('button',{name:'constructor',exact:true}).click()
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.blocks.QuestionCard.4c381122f9a2')),exact:true}).click()
 assert.deepEqual(await page.evaluate(()=>window.answers),[{id:'plan-options',answers:[{id:'plan-review',selected:['constructor']}]}])
 assert.deepEqual(errors,[]);await page.close()
 console.log('Plan options: inherited names stay literal, known actions localize, original selection submits once')
},{entry:'/__plan-option-language-entry.js',vite:{plugins:[{name:'plan-option-fixture',resolveId(id){if(id==='/__plan-option-language-entry.js')return id},load(id){if(id==='/__plan-option-language-entry.js')return entry},configureServer(server){server.middlewares.use('/__plan-option-language',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__plan-option-language','<html><body><div id="root"></div><script type="module" src="/__plan-option-language-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
