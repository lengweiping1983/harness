import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const permissionIds=['allow-once','allow-temporarily','deny']
const suffixes=['f9881e36e9cd','4e9b6b4d7c21','136de7a8c46f']
const catalogs=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const backendCatalog=JSON.parse(readFileSync(new URL('../../harness/i18n/locales/en.json',import.meta.url),'utf8'))
const originalLabels=suffixes.map(suffix=>backendCatalog['python.permissions.'+suffix])
assert.ok(originalLabels.every(label=>typeof label==='string'&&label))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QuestionCard} from '/src/components/chat/blocks/QuestionCard.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.answers=[]
const questions=['__proto__','constructor','toString','ordinary'].map((id,index)=>({id,header:'docs/Development.md // ui-Test 原文 '+id,question:'Original question 原文 '+index,...(index%2===0?{options:[{label:'Original choice 原文'}]}:{})}))
const block={id:'form',kind:'question',at:0,questionId:'fixture',questions}
createRoot(document.getElementById('root')).render(React.createElement(QuestionCard,{block,sessionId:'owner',onAnswer:(id,answers)=>window.answers.push({id,answers})}))
window.permissionAnswers=[]
for(const mode of ['direct','answered','shared','busy']){
 const root=document.createElement('div');document.body.append(root)
 const question={id:'permission',question:'Original terminal command 原文',options:${JSON.stringify(permissionIds.map((id,index)=>({id,label:originalLabels[index]})))}}
 const block={id:mode,kind:'question',at:0,questionId:'permission-'+mode,questions:[question],...(mode==='answered'?{answered:true,answers:[{id:'permission',selected:[question.options[0].label]}]}:{})}
 createRoot(root).render(React.createElement(QuestionCard,{block,sessionId:'owner',readOnly:mode==='shared',busy:mode==='busy',onAnswer:(id,answers)=>window.permissionAnswers.push({id,answers})}))
}
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[];page.setDefaultTimeout(15000)
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
 await page.goto(server.resolvedUrls.local[0]+'__question-key')
 const fields=page.locator('#q-fixture fieldset');await fields.first().waitFor()
 for(const index of [0,2])await fields.nth(index).getByRole('button',{name:'Original choice 原文',exact:true}).click()
 for(const index of [1,3])await fields.nth(index).getByRole('textbox').fill('Original answer 原文 '+index)
 await page.reload();await fields.first().waitFor()
 const storage=await page.evaluate(()=>sessionStorage.getItem('harness.question-draft.v1:'+JSON.stringify(['owner','fixture'])))
 assert.ok(storage)
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const mode of ['direct','answered','shared','busy']){
   const card=page.locator('#q-permission-'+mode)
   await card.getByText('Original terminal command 原文',{exact:true}).waitFor()
   for(const suffix of suffixes){
    const button=card.getByRole('button',{name:catalogs[locale]['ui.components.chat.blocks.QuestionCard.'+suffix],exact:true})
    await button.waitFor()
    assert.equal(await button.isDisabled(),mode!=='direct')
   }
  }
  assert.deepEqual(await page.evaluate(()=>window.permissionAnswers),[])
  const progress=await page.evaluate(()=>window.copy('ui.question.progress',{completed:4,total:4}))
  await page.getByText(progress,{exact:true}).waitFor()
  if(locale==='en')assert.equal(progress,'Answered: 4 / 4')
  assert.deepEqual(await fields.locator('legend').evaluateAll(nodes=>nodes.map(node=>({text:node.textContent,transform:getComputedStyle(node).textTransform}))),
   ['__proto__','constructor','toString','ordinary'].map(id=>({text:'docs/Development.md // ui-Test 原文 '+id,transform:'none'})),
   'Dynamic titles must preserve path and code casing in both text and computed styles')
  for(const index of [0,2])assert.equal(await fields.nth(index).getByRole('button',{name:'Original choice 原文',exact:true}).getAttribute('aria-pressed'),'true')
  for(const index of [1,3])assert.equal(await fields.nth(index).getByRole('textbox').inputValue(),'Original answer 原文 '+index)
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('harness.question-draft.v1:'+JSON.stringify(['owner','fixture']))),storage)
  assert.equal(await page.evaluate(()=>window.answers.length),0)
 }
 await fields.nth(3).getByRole('textbox').fill('')
 const jump=page.getByRole('button',{name:'Go to unanswered: docs/Development.md // ui-Test 原文 ordinary',exact:true});await jump.click()
 assert.equal(await fields.nth(3).getByRole('textbox').evaluate(node=>document.activeElement===node),true)
 await fields.nth(3).getByRole('textbox').fill('Original answer 原文 3')
 const submit=await page.evaluate(()=>window.copy('ui.components.chat.blocks.QuestionCard.0c1762dddc4f'))
 await page.getByRole('button',{name:submit,exact:true}).click()
 assert.deepEqual(await page.evaluate(()=>window.answers),[{id:'fixture',answers:[{id:'__proto__',selected:['Original choice 原文']},{id:'constructor',selected:[],custom:'Original answer 原文 1'},{id:'toString',selected:['Original choice 原文']},{id:'ordinary',selected:[],custom:'Original answer 原文 3'}]}])
 for(const index of permissionIds.keys()){
  await page.locator('#q-permission-direct').getByRole('button',{name:catalogs.en['ui.components.chat.blocks.QuestionCard.'+suffixes[index]],exact:true}).click()
  assert.deepEqual(await page.evaluate(()=>window.permissionAnswers.at(-1)),{id:'permission-direct',answers:[{id:'permission',selected:[originalLabels[index]]}]})
 }
 assert.equal(await page.evaluate(()=>window.permissionAnswers.length),3)
 assert.deepEqual(errors,[]);assert.deepEqual(requests,[])
 console.log('Questions: inherited-name IDs render, restore, retain original answers and submit once; complete progress/jump copy en→zh→en, both themes and widths')
},{entry:'/__question-key-entry.js',vite:{plugins:[{name:'question-key-fixture',resolveId(id){if(id==='/__question-key-entry.js')return id},load(id){if(id==='/__question-key-entry.js')return entry},configureServer(server){server.middlewares.use('/__question-key',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__question-key','<html><body><div id="root"></div><script type="module" src="/__question-key-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
