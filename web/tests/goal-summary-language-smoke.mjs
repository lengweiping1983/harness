import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {GoalDock} from '/src/components/chat/GoalDock.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const variant=Number(new URLSearchParams(location.search).get('variant'))
const goal={id:'fixture-goal',revision:1,objective:'Original objective 原文😀',criteria:['Original criterion 原文😀'],phase:'paused',roundsStarted:2,maxGoalRounds:50,tokensUsed:1234,tokenBudget:variant&1?5000:null,tokensReserved:variant&2?67:0,tokensUncertain:42,elapsedSeconds:5,timingActive:false}
createRoot(document.getElementById('root')).render(React.createElement(GoalDock,{sessionId:'fixture',state:{stateVersion:1,activation:'disarmed',goal},running:!!(variant&2),openRequest:1}))
`
await browserScenario(async({server,browser})=>{
 for(const variant of [0,1,2,3]){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  const errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
  await page.goto(server.resolvedUrls.local[0]+'__goal-summary?variant='+variant)
  const matrix=async check=>{
   for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
    await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
    await check(locale);assert.equal(requests.length,0)
   }
  }
  await matrix(async locale=>{
   const used=(1234).toLocaleString(locale),budget=(5000).toLocaleString(locale)
   let usage=locale==='zh-CN'?`已用 token：${used} · ${variant&1?'总预算：'+budget:'未设总上限'}`:`Token usage: ${used} · ${variant&1?'Total budget: '+budget:'No total limit set'}`
   if(variant&2)usage+=locale==='zh-CN'?' · 预留：67':' · Reserved: 67'
   await page.getByText(usage,{exact:true}).waitFor()
   await page.getByText(locale==='zh-CN'?'有 42 token 的预留无法确认最终用量，仍占用预算。':'Final usage is unconfirmed for reserved tokens (42); they still count against the budget.',{exact:true}).waitFor()
  })
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.GoalDock.127636b82d8b')),exact:true}).click()
  const draft=await page.getByRole('textbox').inputValue()
  await page.getByText(await page.evaluate(()=>window.copy('ui.goal.optionalLimits')),{exact:true}).click()
  await matrix(async locale=>{
   const zh='修改目标或验收条件后需重新验证；已用轮次和 token 不会清零。'+(variant&2?'当前轮次会继续，后续模型请求读取新目标。':'')+'保存后保持当前暂停或推进状态。'
   const en='Changing the goal or acceptance criteria requires re-verification; used turns and tokens are not reset. '+(variant&2?'The current turn continues; subsequent model requests read the new goal. ':'')+'Saving preserves the current paused or active state.'
   await page.getByText(locale==='zh-CN'?zh:en,{exact:true}).waitFor()
   assert.equal(await page.getByRole('textbox').inputValue(),draft)
   for(const rounds of [50,500,5000]){
    const button=page.getByRole('group',{name:await page.evaluate(()=>window.copy('ui.goal.runScale')),exact:true}).getByRole('button',{name:await page.evaluate(rounds=>window.copy('ui.goal.scale'+rounds),rounds),exact:true})
    await button.click();assert.equal(await button.getAttribute('aria-pressed'),'true')
   }
  })
  assert.deepEqual(errors,[]);await page.close();console.log('Goal summary '+variant+': complete bilingual usage and edit text, draft preserved, no requests')
 }
},{entry:'/__goal-summary-entry.js',vite:{plugins:[{
 name:'goal-summary-fixture',resolveId(id){if(id==='/__goal-summary-entry.js')return id},load(id){if(id==='/__goal-summary-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__goal-summary',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__goal-summary','<html><body><div id="root"></div><script type="module" src="/__goal-summary-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
