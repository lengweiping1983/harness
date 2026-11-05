import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {browserScenario} from './browser-scenario.mjs'
const longCommand = 'python3 -c "import xml.etree.ElementTree as ET; ET.parse(\'compound-bow.svg\'); print(\'XML OK\')"'
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
 ...(index<4?[{kind:'tool',id:'tool-'+index,callId:'call-'+index,turn:1,at:1,name:index===0?'edit':index===1?'bash':index%2?'acceptance_evidence':'get_goal',status:'ok',input:index===0?{file_path:'compound-bow.svg'}:index===1?{command:${JSON.stringify(longCommand)}}:{}}]:[])
])
blocks.push({kind:'assistant',id:'reply',turn:1,at:1,text:'Visible answer',streaming:true})
createRoot(document.getElementById('root')).render(React.createElement('div',{style:{height:700,display:'flex'}},React.createElement(Conversation,{blocks,turns:{1:{turn:1,startedAt:1,startSeq:0}},running:true,streaming:true,connection:'open',readOnly:true})))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__process-group')
 const group=page.locator('[data-tool-run-turn="1"] button')
 await group.waitFor()
 for(const locale of ['en','zh-CN','en'])for(const [dark,width] of [[false,390],[true,1440],[false,1440]]){
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
  await group.evaluate(node=>Promise.all(node.getAnimations({subtree:true}).filter(animation=>animation.effect?.getTiming().iterations!==Infinity).map(animation=>animation.finished)))
  const groupGap=await group.evaluate(node=>{
   const arrow=node.querySelector('.lucide-chevron-right'),text=arrow.previousElementSibling,range=document.createRange()
   range.selectNodeContents(text)
   const rectangles=range.getClientRects(),last=rectangles[rectangles.length-1]
   const textRight=text.scrollWidth>text.clientWidth?text.getBoundingClientRect().right:Math.min(last.right,text.getBoundingClientRect().right)
   return arrow.getBoundingClientRect().left-textRight
  })
  assert.ok(groupGap>=5&&groupGap<=7,`Process disclosure must follow summary text: ${groupGap}`)
  for(const decorated of [true,false]){
  const layout=await page.evaluate(decorated=>{
   const controls=['thought-0','tool-0','thought-1','tool-1'].map(id=>document.querySelector(`[data-row-id="${id}"] :is(summary, button)`))
   return controls.map(node=>{
    if(node.tagName==='BUTTON')node.classList.toggle('conversation-activity',decorated)
    const row=node.closest('[data-row-id]'),rect=node.getBoundingClientRect(),rowRect=row.getBoundingClientRect()
    const arrow=node.querySelector('.lucide-chevron-right').getBoundingClientRect()
    const icon=node.querySelector('svg').getBoundingClientRect(),label=node.querySelector('span').getBoundingClientRect()
    const text=node.querySelector('.lucide-chevron-right').previousElementSibling,range=document.createRange()
    range.selectNodeContents(text)
    const rectangles=range.getClientRects(),last=rectangles[rectangles.length-1]
    // 截断时省略号不是原始文本的 Range，按承载省略号的可见盒子计算。
    const textRight=text.scrollWidth>text.clientWidth?text.getBoundingClientRect().right:Math.min(last.right,text.getBoundingClientRect().right)
    return {top:rect.top,bottom:rect.bottom,height:rect.height,outer:rowRect.height-rect.height,arrowGap:arrow.left-textRight,
     iconOffset:(icon.top+icon.bottom-rect.top-rect.bottom)/2,labelOffset:(label.top+label.bottom-rect.top-rect.bottom)/2,arrowOffset:(arrow.top+arrow.bottom-rect.top-rect.bottom)/2}
   })
  },decorated)
  for(const [index,row] of layout.entries()){
   assert.ok(row.height>=24&&row.height<=24.5,`Compact activity hit area: ${row.height}`)
   assert.ok(Math.abs(row.outer)<0.5,`Repeated vertical padding: ${row.outer}`)
   assert.ok(row.arrowGap>=5&&row.arrowGap<=7,`Disclosure arrow must follow visible text: ${row.arrowGap}`)
   for(const offset of [row.iconOffset,row.labelOffset,row.arrowOffset])assert.ok(Math.abs(offset)<0.5,`Icon, label and arrow must share one row: ${offset}`)
   if(index)assert.ok(Math.abs(row.top-layout[index-1].bottom)<0.5,'Adjacent process rows must not leave blank bands')
  }
  }
  await page.locator('[data-row-id="tool-0"] button, [data-row-id="tool-1"] button').evaluateAll(nodes=>nodes.forEach(node=>node.classList.add('conversation-activity')))
  if(locale==='zh-CN'&&!dark&&width===1440){
   const directory=new URL('../../output/',import.meta.url)
   await mkdir(directory,{recursive:true})
   await group.evaluate(node=>node.blur())
   await page.evaluate(()=>Promise.all(document.getAnimations().filter(animation=>animation.effect?.getTiming().iterations!==Infinity).map(animation=>animation.finished)))
   const clip=await page.evaluate(()=>{
    const first=document.querySelector('[data-tool-run-turn="1"]').getBoundingClientRect(),last=document.querySelector('[data-row-id="thought-4"]').getBoundingClientRect()
    return {x:first.x-4,y:first.y-4,width:first.width+8,height:last.bottom-first.y+8}
   })
   await page.screenshot({path:fileURLToPath(new URL('process-group-fixed.png',directory)),clip})
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
