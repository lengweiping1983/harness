import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import '/@vite/client'
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ScreenshotCrop} from '/src/components/chat/ScreenshotCrop.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(ScreenshotCrop,{source:'/missing-image.png',onConfirm:async()=>{throw Error('Unexpected confirm')},onCancel:()=>{}}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();let reads=0;const errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)})
 await page.route('**/missing-image.png',route=>{reads++;return route.fulfill({status:404,body:'missing'})})
 await page.goto(server.resolvedUrls.local[0]+'__screenshot-error');await page.getByRole('alert').waitFor()
 for(const locale of ['en','zh-CN','en']) for(const theme of ['light','dark']) for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
  const expected=await page.evaluate(()=>window.copy('ui.screenshot.imageError'))
  await page.getByText(expected,{exact:true}).waitFor();assert.equal(reads,1)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Screenshot error: saved image-load failure changes language without reloading the image')

},{entry:'/__screenshot-error-entry.js',vite:{plugins:[{name:'screenshot-error-language-fixture',resolveId(id){if(id==='/__screenshot-error-entry.js')return id},load(id){if(id==='/__screenshot-error-entry.js')return entry},configureServer(server){server.middlewares.use('/__screenshot-error',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__screenshot-error','<html><body><div id="root"></div><script type="module" src="/__screenshot-error-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
