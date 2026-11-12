import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {UserBlock} from '/src/components/chat/blocks/MessageBlocks.tsx'
import {blocksFromDetail} from '/src/store/chat.ts'
import {setLocale,useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale
const attachments=[{attachmentId:'a'.repeat(64),name:'Original.txt',mediaType:'text/plain',size:5,isImage:false}]
const texts=['Original before\\n\\n---\\nThis message includes Original after','Original before\\n\\n---\\n本条消息附带 Original after','']
const blocks=blocksFromDetail({header:{id:'owner'},events:[],usage:{},stats:{},todos:[],timeline:[],messages:texts.map((text,index)=>({kind:'message',role:'user',source_seq:index+1,text,attachments}))})
function Fixture(){useLocale();return React.createElement('main',null,blocks.map((block,index)=>React.createElement('section',{'data-original':index,key:index},React.createElement(UserBlock,{block,hideActions:true,readOnly:true}))))}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(10000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__attachment-original')
 for(const language of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({language,dark})=>{window.locale(language);document.documentElement.classList.toggle('dark',dark)},{language,dark})
  assert.equal(await page.locator('[data-original]').count(),3)
  for(const index of [0,1])await page.locator('[data-original="'+index+'"]').getByText('Original after',{exact:false}).waitFor()
  for(const index of [0,1,2])await page.locator('[data-original="'+index+'"]').getByText('Original.txt',{exact:true}).waitFor()
 }
 assert.deepEqual(errors,[]);await page.close()
 console.log('Original attachment markers and attachment-only messages survive snapshot rendering across languages, themes and sizes')
},{entry:'/__attachment-original-entry.js',vite:{plugins:[{name:'attachment-original-fixture',resolveId(id){if(id==='/__attachment-original-entry.js')return id},load(id){if(id==='/__attachment-original-entry.js')return entry},configureServer(server){server.middlewares.use('/__attachment-original',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__attachment-original','<html><body><div id="root"></div><script type="module" src="/__attachment-original-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
