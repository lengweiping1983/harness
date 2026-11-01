import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {FileChangesCard} from '/src/components/chat/blocks/FileChangesCard.tsx'
import {DeliverablesCard} from '/src/components/chat/blocks/DeliverablesCard.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;setLocale('en')
const files=[{path:'/fixture/one/Original-{name}.txt',operations:1},{path:'/fixture/two/other.txt',operations:1}]
createRoot(document.getElementById('root')).render(React.createElement('main',null,
 ...[0,1,2].map(count=>React.createElement(FileChangesCard,{key:count,id:'count-'+count,files:files.slice(0,count),sessionId:null,pending:false})),
 React.createElement(DeliverablesCard,{block:{id:'outputs',kind:'deliverables',at:0,workspaceRoot:'/fixture',files:files.map(file=>({path:file.path,name:file.path.split('/').pop()}))}})))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__file-count')
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const count of [0,1,2])await page.getByText(locale==='en'?('Modified '+count+(count===1?' file':' files')):('已修改 '+count+' 个文件'),{exact:true}).waitFor()
  await page.getByText(locale==='en'?'2 directories':'2 个目录',{exact:true}).waitFor()
  await page.getByText('one/Original-{name}.txt',{exact:true}).first().waitFor()
 }
 assert.deepEqual(errors,[])
 console.log('File cards: 0/1/2 counts, original names and directories across en/zh/en, themes and widths passed')
},{entry:'/__file-count-entry.js',vite:{plugins:[{name:'file-count-fixture',resolveId(id){if(id==='/__file-count-entry.js')return id},load(id){if(id==='/__file-count-entry.js')return entry},configureServer(server){server.middlewares.use('/__file-count',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__file-count','<html><body><div id="root"></div><script type="module" src="/__file-count-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
