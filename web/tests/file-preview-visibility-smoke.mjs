import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {FilePreview} from '/src/components/inspector/FilePreview.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.reads=[];window.locale=setLocale
window.fetch=async(_url,options)=>new Promise(resolve=>window.reads.push({signal:options.signal,resolve:text=>resolve(new Response(text))}))
const root=createRoot(document.getElementById('root'))
window.show=active=>root.render(React.createElement(FilePreview,{active,path:'original.txt',root:'/fixture',focusContent:false}))
window.show(true)
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__preview-visible')
 await page.waitForFunction(()=>window.reads.length===1)
 await page.evaluate(()=>window.show(false))
 await page.waitForFunction(()=>window.reads[0].signal.aborted)
 await page.evaluate(()=>window.reads[0].resolve('STALE CONTENT'))
 assert.equal(await page.getByText('STALE CONTENT',{exact:true}).count(),0)
 for(const locale of ['en','zh-CN','en'])await page.evaluate(locale=>window.locale(locale),locale)
 assert.equal(await page.evaluate(()=>window.reads.length),1)
 await page.evaluate(()=>window.show(true))
 await page.waitForFunction(()=>window.reads.length===2)
 await page.evaluate(()=>window.reads[1].resolve('Original content 原文'))
 await page.getByText('Original content 原文',{exact:true}).waitFor()
 assert.deepEqual(errors,[]);await page.close()
},{entry:'/__preview-visible-entry.js',vite:{plugins:[{name:'preview-visible',resolveId(id){if(id==='/__preview-visible-entry.js')return id},load(id){if(id==='/__preview-visible-entry.js')return entry},configureServer(server){server.middlewares.use('/__preview-visible',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__preview-visible','<html><body><div id="root"></div><script type="module" src="/__preview-visible-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
