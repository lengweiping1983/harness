import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {GoalEvidence} from '/src/components/chat/GoalEvidence.tsx'
import {api} from '/src/lib/api.ts'
import {RenameDialog} from '/src/components/overlays/RenameDialog.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale
api.goalEvidence=async()=>({ok:true,data:{evidence:{text:'Original 原文\\n'.repeat(100),offset:0,totalChars:40000,nextOffset:20000}}})
const root=createRoot(document.getElementById('root'))
window.show=kind=>root.render(kind==='evidence'?React.createElement(GoalEvidence,{sessionId:'fixture',goal:{id:'goal',objective:'Original',phase:'complete',criteria:['Original'],completion:[{criterion:1,summary:'Original',evidence:[{sourceSeq:1,name:'read_file',input:'/original/'.repeat(80)}]}]}}):React.createElement(RenameDialog,{target:{kind:'session',id:'fixture',value:'Original 原文'},onClose:()=>{window.closed=true}}))
`
await browserScenario(async({server,browser})=>{
 for(const kind of ['rename','evidence'])for(const language of ['en','zh-CN'])for(const width of [390,1440])for(const size of [10,16,22]){
  const page=await browser.newPage({viewport:{width,height:240}}),errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0]+'__overlay-height')
  await page.evaluate(({language,size,kind})=>{window.locale(language);document.documentElement.style.fontSize=size+'px';window.show(kind)},{language,size,kind})
  if(kind==='evidence')await page.getByRole('button').first().click()
  const dialog=page.getByRole('dialog');await dialog.waitFor()
  for(const control of [kind==='rename'?dialog.getByRole('textbox'):dialog.locator('pre'),dialog.getByRole('button').last()]){
   await control.scrollIntoViewIfNeeded()
   assert.equal(await control.evaluate(node=>{const r=node.getBoundingClientRect();const x=r.left+r.width/2,y=r.top+r.height/2;return y>=0&&y<innerHeight&&node.contains(document.elementFromPoint(x,y))}),true,kind+' '+language+' '+width+' '+size)
  }
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Rename and goal evidence controls remain reachable in short viewports at all supported font endpoints, both widths and languages')
},{entry:'/__overlay-height-entry.js',vite:{plugins:[{name:'overlay-height-fixture',resolveId(id){if(id==='/__overlay-height-entry.js')return id},load(id){if(id==='/__overlay-height-entry.js')return entry},configureServer(server){server.middlewares.use('/__overlay-height',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__overlay-height','<html><body><div id="root"></div><script type="module" src="/__overlay-height-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
