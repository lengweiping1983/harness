import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AssistantBlock} from '/src/components/chat/blocks/MessageBlocks.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
await setLocale('en');window.locale=setLocale
createRoot(document.getElementById('root')).render(React.createElement(AssistantBlock,{hideActions:true,block:{kind:'assistant',id:'preview',at:1,text:'Original preview 原文',previewTruncated:true}}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__member-preview')
 await page.getByText('Original preview 原文',{exact:true}).waitFor()
 const key='ui.team.preview.truncated'
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  await page.getByText(resources[locale][key],{exact:true}).waitFor()
  await page.getByText('Original preview 原文',{exact:true}).waitFor()
  assert.equal(await page.locator('[aria-busy="true"]').count(),0)
 }
 assert.deepEqual(errors,[]);await page.close()
},{entry:'/__member-preview-entry.js',vite:{plugins:[{name:'member-preview',resolveId(id){if(id==='/__member-preview-entry.js')return id},load(id){if(id==='/__member-preview-entry.js')return entry},configureServer(server){server.middlewares.use('/__member-preview',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__member-preview','<html><body><div id="root"></div><script type="module" src="/__member-preview-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
