import assert from 'node:assert/strict'
import {NativeDialogs} from '../../desktop/dialogs.mjs'
import {browserScenario} from './browser-scenario.mjs'

const snapshots=[]
const producer=new NativeDialogs()
for(const key of ['browser.desktop.confirmClose','browser.desktop.confirmLeave']){
 producer.open(key,'beforeunload','','https://fixture.invalid','',()=>{},{key,params:{}})
 snapshots.push(producer.snapshot(key))
}
const original='网页可能有尚未保存的内容。是否关闭此标签？'
producer.open('external','beforeunload',original,'https://fixture.invalid','',()=>{})
snapshots.push(producer.snapshot('external'))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {BrowserDialogPanel} from '/src/components/inspector/BrowserDialogPanel.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
const snapshots=${JSON.stringify(snapshots)}
window.answers=[];window.locale=setLocale;setLocale('en')
const root=createRoot(document.getElementById('root'))
window.show=index=>{const dialog={...snapshots[index],page_id:'Original-target'};root.render(React.createElement(BrowserDialogPanel,{key:dialog.id,dialog,control:true,pending:false,onRespond:(accept,text)=>window.answers.push({id:dialog.id,accept,text})}))}
window.show(0)
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__native-dialog')
 for(let index=0;index<snapshots.length;index++){
  await page.evaluate(index=>window.show(index),index)
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   const expected=index===2?original:locale==='en'?(index===0?'This page may have unsaved changes. Close this tab?':'This page may have unsaved changes. Leave this page?'):(index===0?'网页可能有尚未保存的内容。是否关闭此标签？':'网页可能有尚未保存的内容。是否离开当前页面？')
   await page.locator('blockquote').getByText(expected,{exact:true}).waitFor()
   assert.equal((await page.evaluate(()=>window.answers)).length,index)
  }
  await page.getByRole('button',{name:'Stay on the web page',exact:true}).click()
  const answers=await page.evaluate(()=>window.answers)
  assert.equal(answers.length,index+1)
  assert.deepEqual(answers[index],{id:snapshots[index].id,accept:false,text:undefined})
 }
 assert.deepEqual(errors,[])
 console.log('Native dialog producer -> panel: owned close/leave resources follow en/zh/en; website text unchanged; no automatic answers; explicit responses keep identity')
},{entry:'/__native-dialog-entry.js',vite:{plugins:[{name:'native-dialog-language-fixture',resolveId(id){if(id==='/__native-dialog-entry.js')return id},load(id){if(id==='/__native-dialog-entry.js')return entry},configureServer(server){server.middlewares.use('/__native-dialog',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__native-dialog','<html><body><div id="root"></div><script type="module" src="/__native-dialog-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
