import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entryPath='/__settings-icons-entry.js'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {SettingsDialog} from '/src/components/overlays/SettingsDialog.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.language=setLocale
useAppStore.getState().setSettingsOpen(true)
createRoot(document.getElementById('root')).render(React.createElement(SettingsDialog))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__settings-icons')
for(const group of ['agents','connections','advanced']){
 const button=page.locator('nav button[aria-controls="settings-group-'+group+'"]').first()
 if(await button.getAttribute('aria-expanded')==='false')await button.click()
}
 for(const language of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:1000})
  await page.evaluate(async({language,dark})=>{await window.language(language);document.documentElement.classList.toggle('dark',dark)},{language,dark})
  const buttons=page.locator('nav button[data-page]:visible')
  assert.equal(await buttons.count(),15)
  assert.equal(await page.locator('nav button[data-page="context"]').count(),0)
  const icons=await buttons.evaluateAll(elements=>elements.map(button=>{
   const svg=button.querySelector('svg'),rect=svg.getBoundingClientRect()
   return {page:button.dataset.page,label:button.textContent.trim(),drawing:svg.innerHTML,width:rect.width,height:rect.height}
  }))
  assert.equal(new Set(icons.map(icon=>icon.drawing)).size,15,'Every settings page has a distinct rendered icon')
  for(const icon of icons){assert.ok(icon.label);assert.ok(Math.abs(icon.width-14)<0.5);assert.ok(Math.abs(icon.height-14)<0.5)}
  assert.equal(await page.getByRole('dialog').evaluate(el=>el.scrollWidth<=el.clientWidth),true)
  if(language==='zh-CN'&&!dark&&width===1440&&process.env.HARNESS_TEST_SETTINGS_ICONS_SCREENSHOT)await page.screenshot({path:process.env.HARNESS_TEST_SETTINGS_ICONS_SCREENSHOT})
 }
 assert.deepEqual(errors,[])
 console.log('Settings navigation: all 15 rendered icons are distinct, labeled and consistently sized across bilingual themes and widths')
},{entry:entryPath,vite:{plugins:[{name:'settings-icons-fixture',resolveId(id){if(id===entryPath)return id},load(id){if(id===entryPath)return entry},configureServer(server){server.middlewares.use('/__settings-icons',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__settings-icons','<html><body><div id="root"></div><script type="module" src="'+entryPath+'"></script></body></html>'))}catch(error){next(error)}})}}]}})
