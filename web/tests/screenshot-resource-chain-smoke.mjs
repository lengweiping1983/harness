import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ScreenshotCrop} from '/src/components/chat/ScreenshotCrop.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.actions=[]
const source=document.createElement('canvas');source.width=640;source.height=480;const ctx=source.getContext('2d');ctx.fillStyle='#336699';ctx.fillRect(0,0,640,480)
createRoot(document.getElementById('root')).render(React.createElement(ScreenshotCrop,{source:source.toDataURL('image/png'),initialFullSelection:true,onConfirm:async()=>window.actions.push('confirm'),onCancel:()=>window.actions.push('cancel')}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[]
 page.on('pageerror',error=>errors.push(error.message));await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500})})
 await page.goto(server.resolvedUrls.local[0]+'__screenshot-resource-chain')
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const dialog=page.getByRole('dialog',{name:resources[locale]['ui.components.chat.ScreenshotCrop.bf8275a62399'],exact:true});await dialog.waitFor()
  for(const tool of ['rectangle','ellipse','arrow','pen','mosaic','text']){
   const key='ui.screenshot.'+tool;assert.ok(resources[locale][key]);const button=dialog.getByRole('button',{name:resources[locale][key],exact:true})
   assert.equal(await button.getAttribute('title'),resources[locale][key]);await button.click();assert.equal(await button.getAttribute('aria-pressed'),'true')
   for(const index of [0,1,2]){const key='ui.screenshot.width'+index;assert.ok(resources[locale][key]);const option=dialog.getByRole('button',{name:resources[locale][key],exact:true});await option.click();assert.equal(await option.getAttribute('aria-pressed'),'true')}
   if(tool==='mosaic')assert.equal(await dialog.locator('.screenshot-color').count(),0)
   else for(const index of [0,1,2,3,4,5]){const key='ui.screenshot.color'+index;assert.ok(resources[locale][key]);const option=dialog.getByRole('button',{name:resources[locale][key],exact:true});await option.click();assert.equal(await option.getAttribute('aria-pressed'),'true')}
  }
  const last=dialog.locator('.screenshot-options button[aria-pressed=true]');assert.equal(await last.count(),2)
  assert.deepEqual(await page.evaluate(()=>window.actions),[])
 }
 assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);await page.close();console.log('Screenshot tools, colors and widths: bilingual titles/accessibility, actual selection, mosaic visibility, theme/width matrix without capture/save actions')
},{entry:'/__screenshot-resource-chain-entry.js',vite:{plugins:[{name:'screenshot-resource-chain',resolveId(id){if(id==='/__screenshot-resource-chain-entry.js')return id},load(id){if(id==='/__screenshot-resource-chain-entry.js')return entry},configureServer(server){server.middlewares.use('/__screenshot-resource-chain',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__screenshot-resource-chain','<html><body><div id="root"></div><script type="module" src="/__screenshot-resource-chain-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
