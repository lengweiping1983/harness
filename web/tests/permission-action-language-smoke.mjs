import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QuestionCard} from '/src/components/chat/blocks/QuestionCard.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.answers=[]
const root=createRoot(document.getElementById('root'))
let version=0
window.reset=()=>{
 const block={id:'permission',kind:'question',at:0,questionId:'permission-'+(++version),questions:[{
  id:'permission',question:'Original operation 原文',operation:{tool:'OriginalTool',cwd:'/Original/原文',effect:'execute'},options:[
   {id:'allow-once',label:'Original once 原文'},
   {id:'allow-temporarily',label:'Original temporary 原文'},
   {id:'deny',label:'Original deny 原文'}]}]}
 const render=current=>root.render(React.createElement(QuestionCard,{key:version,block:current,sessionId:null,onAnswer:(id,answers)=>{
  window.answers.push({id,answers});render({...current,answered:true,answers})
 }}))
 render(block)
}
window.reset()
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(10000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__permission-action')
 const keys=['f9881e36e9cd','4e9b6b4d7c21','136de7a8c46f']
 const originals=['Original once 原文','Original temporary 原文','Original deny 原文']
 for(const dark of [false,true])for(const width of [390,1440])for(let action=0;action<3;action++){
  await page.setViewportSize({width,height:900});await page.evaluate(()=>window.reset())
  const before=await page.evaluate(()=>window.answers.length)
  for(const language of ['en','zh-CN','en']){
   await page.evaluate(({language,dark})=>{window.locale(language);document.documentElement.classList.toggle('dark',dark)},{language,dark})
   for(const key of keys)await page.getByRole('button',{name:await page.evaluate(key=>window.copy('ui.components.chat.blocks.QuestionCard.'+key),key),exact:true}).waitFor()
   await page.getByText('OriginalTool',{exact:true}).waitFor()
   await page.getByText('Original operation 原文',{exact:true}).waitFor({state:'visible'})
   await page.getByText('/Original/原文',{exact:true}).waitFor()
   await page.getByText(await page.evaluate(()=>window.copy('ui.permissions.scopeHint')),{exact:true}).waitFor()
   assert.equal(await page.evaluate(()=>window.answers.length),before)
  }
  await page.getByRole('button',{name:await page.evaluate(key=>window.copy('ui.components.chat.blocks.QuestionCard.'+key),keys[action]),exact:true}).click()
  await page.waitForFunction(before=>window.answers.length===before+1,before)
  assert.deepEqual(await page.evaluate(()=>window.answers.at(-1).answers),[{id:'permission',selected:[originals[action]]}])
  await page.evaluate(()=>window.locale('zh-CN'))
  for(const key of keys)assert.equal(await page.getByRole('button',{name:await page.evaluate(key=>window.copy('ui.components.chat.blocks.QuestionCard.'+key),key),exact:true}).isDisabled(),true)
  assert.equal(await page.evaluate(()=>window.answers.length),before+1)
 }
 assert.deepEqual(errors,[]);await page.close()
 console.log('Permission action identities localize before and after answering; locale changes never submit and every action sends its original label once')
},{entry:'/__permission-action-entry.js',vite:{plugins:[{name:'permission-action-fixture',resolveId(id){if(id==='/__permission-action-entry.js')return id},load(id){if(id==='/__permission-action-entry.js')return entry},configureServer(server){server.middlewares.use('/__permission-action',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__permission-action','<html><body><div id="root"></div><script type="module" src="/__permission-action-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
