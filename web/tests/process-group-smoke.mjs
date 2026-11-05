import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Conversation} from '/src/components/chat/Conversation.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale
useAppStore.setState({activeSessionId:'process-fixture'})
const blocks=[299,371,3100,916,730].flatMap((count,index)=>[
 {kind:'thinking',id:'thought-'+index,turn:1,at:1,text:'原'.repeat(count)},
 ...(index<4?[{kind:'tool',id:'tool-'+index,callId:'call-'+index,turn:1,at:1,name:index%2?'acceptance_evidence':'get_goal',status:'ok',input:{}}]:[])
])
blocks.push({kind:'assistant',id:'reply',turn:1,at:1,text:'Visible answer',streaming:true})
createRoot(document.getElementById('root')).render(React.createElement('div',{style:{height:700,display:'flex'}},React.createElement(Conversation,{blocks,turns:{1:{turn:1,startedAt:1,startSeq:0}},running:true,streaming:true,connection:'open',readOnly:true})))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__process-group')
 const group=page.locator('[data-tool-run-turn="1"] button')
 await group.waitFor()
 for(const locale of ['en','zh-CN','en'])for(const [dark,width] of [[false,390],[true,1440]]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  await page.waitForFunction(locale=>document.querySelector('[data-tool-run-turn="1"] button')?.textContent.includes(locale==='en'?'Thinking segments: 5':'5 段思考'),locale)
  assert.equal(await group.innerText().then(text=>/characters|字符/i.test(text)),false)
  assert.equal(await page.locator('[data-tool-run-turn]').count(),1)
  const caret=page.locator('.streaming-caret')
  await caret.waitFor()
  for(const reducedMotion of ['no-preference','reduce','no-preference']){
   await page.emulateMedia({reducedMotion})
   const appearance=await caret.evaluate(node=>{const style=getComputedStyle(node,'::after');return {animation:style.animationName,display:style.display,content:style.content,width:parseFloat(style.width)}})
   assert.equal(appearance.animation,reducedMotion==='reduce'?'none':'blink')
   const shimmer=await page.locator('.shimmer-bar').evaluate(node=>{const style=getComputedStyle(node,'::after');return {animation:style.animationName,transform:style.transform}})
   assert.equal(shimmer.animation,reducedMotion==='reduce'?'none':'shimmer')
   if(reducedMotion==='reduce')assert.equal(shimmer.transform,'none')
   assert.equal(appearance.display,'inline-block');assert.equal(appearance.content,'""');assert.ok(appearance.width>0)
   assert.equal(await page.getByText('Visible answer',{exact:true}).count(),1)
  }
  assert.equal(await page.locator('details').count(),0)
  await group.focus();await page.keyboard.press('Enter')
  await page.waitForFunction(()=>document.querySelector('[data-tool-run-turn="1"] button')?.getAttribute('aria-expanded')==='true')
  const thought=page.locator('[data-row-id="thought-0"] details')
  await thought.waitFor()
  assert.equal(await thought.evaluate(node=>node.open),false)
  const layout=await page.evaluate(()=>{
   const controls=['thought-0','tool-0','thought-1','tool-1'].map(id=>document.querySelector(`[data-row-id="${id}"] .conversation-activity`))
   return controls.map(node=>{
    const row=node.closest('[data-row-id]'),rect=node.getBoundingClientRect(),rowRect=row.getBoundingClientRect()
    const arrow=node.querySelector('.lucide-chevron-right').getBoundingClientRect()
    return {top:rect.top,bottom:rect.bottom,height:rect.height,outer:rowRect.height-rect.height,right:rect.right-arrow.right}
   })
  })
  for(const [index,row] of layout.entries()){
   assert.ok(row.height>=24&&row.height<=28.5,`Compact activity hit area: ${row.height}`)
   assert.ok(Math.abs(row.outer)<0.5,`Repeated vertical padding: ${row.outer}`)
   assert.ok(row.right>=3&&row.right<=5,`Disclosure arrow must align with the right inset: ${row.right}`)
   if(index)assert.ok(Math.abs(row.top-layout[index-1].bottom)<0.5,'Adjacent process rows must not leave blank bands')
  }
  await thought.locator('summary').focus();await page.keyboard.press('Enter')
  await page.waitForFunction(()=>document.querySelector('[data-row-id="thought-0"] details')?.open===true)
  assert.equal(await thought.locator('.md').textContent(),'原'.repeat(299))
  await thought.locator('summary').click()
  assert.equal(await thought.evaluate(node=>node.open),false)
  await group.click()
  await page.waitForFunction(()=>!document.querySelector('[data-row-id="thought-0"]'))
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Process summary: 9 blocks grouped; keyboard expansion and live reduced-motion caret/shimmer preferences preserve content across languages, themes and widths')
},{entry:'/__process-group-entry.js',vite:{plugins:[{name:'process-group-fixture',resolveId(id){if(id==='/__process-group-entry.js')return id},load(id){if(id==='/__process-group-entry.js')return entry},configureServer(server){server.middlewares.use('/__process-group',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__process-group','<html><body><div id="root"></div><script type="module" src="/__process-group-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
