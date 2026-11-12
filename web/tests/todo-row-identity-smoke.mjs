import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TodoBlock} from '/src/components/chat/blocks/TodoBlock.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
const root=createRoot(document.getElementById('root'))
window.locale=setLocale
window.render=(count)=>root.render(React.createElement(TodoBlock,{todos:Array.from({length:count},(_,index)=>({content:'Original 重复 {name}',status:index===0?'completed':'pending'}))}))
window.render(3)
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.goto(server.resolvedUrls.local[0]+'__todo-identity')
 await page.locator('li').first().waitFor()
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const count of [3,2,4,1]){
   await page.evaluate(count=>window.render(count),count)
   await page.waitForFunction(count=>document.querySelectorAll('li').length===count,count)
   assert.deepEqual(await page.locator('li').allTextContents(),Array(count).fill('Original 重复 {name}'))
   assert.equal(await page.locator('li .line-through').count(),1)
  }
 }
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Todo rows: duplicate content, list replacement and bilingual theme/width matrix passed without React warnings')
},{entry:'/__todo-identity-entry.js',vite:{plugins:[{name:'todo-row-identity',resolveId(id){if(id==='/__todo-identity-entry.js')return id},load(id){if(id==='/__todo-identity-entry.js')return entry},configureServer(server){server.middlewares.use('/__todo-identity',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__todo-identity','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__todo-identity-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
