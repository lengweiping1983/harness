import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {FilePreview} from '/src/components/inspector/FilePreview.tsx'
import {ImageLightbox} from '/src/components/markdown/ImageLightbox.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale
window.label=key=>t(key)
window.open=()=>useAppStore.getState().openLightbox('/fixture.svg','fixture')
setLocale('en');createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement(FilePreview,{path:'fixture.txt',root:'/fixture',focusContent:false}),React.createElement(ImageLightbox)))
`
const html='<html><body><div id="root"></div><script type="module" src="/__preview-ime-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/v1/file?*',route=>route.fulfill({contentType:'text/plain',body:'one\ntwo\nthree'}))
 await page.route('**/fixture.svg',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'}))
 await page.goto(server.resolvedUrls.local[0]+'__preview-ime');await page.locator('[data-file-source]').waitFor()
 for(const language of ['en','zh-CN','en']){
  await page.evaluate(language=>window.locale(language),language)
  for(const flags of [{isComposing:true},{keyCode:229}]){
   await page.evaluate(()=>window.open());await page.getByRole('dialog').waitFor()
   await page.evaluate(flags=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,...flags})),flags)
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(resolve)))
   assert.equal(await page.getByRole('dialog').count(),1,'IME Escape must not close the image')
   await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'})
  }
  const line=page.locator('#file-line-location');await line.fill('2')
  for(const flags of [{isComposing:true},{keyCode:229}]){
   const accepted=await line.evaluate((element,flags)=>element.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true,...flags})),flags)
   assert.equal(accepted,false,'IME Enter must prevent implicit form submission')
  }
  await line.dispatchEvent('compositionstart')
  await line.press('Enter')
  assert.equal(await page.locator('[data-file-highlight]').count(),0,'Composition Enter must not submit a line jump')
  await line.dispatchEvent('compositionend');await line.press('Enter')
  assert.equal(await page.locator('[data-file-highlight="2-2"]').count(),1,'Ordinary Enter must still jump')
  await page.reload();await page.locator('[data-file-source]').waitFor()
 }
 assert.deepEqual(errors,[])
 console.log('Preview IME: image Escape (composition and 229), composing line submission, normal Escape/Enter and repeated bilingual switching passed')
},{entry:'/__preview-ime-entry.js',vite:{plugins:[{name:'preview-ime-fixture',resolveId(id){if(id==='/__preview-ime-entry.js')return id},load(id){if(id==='/__preview-ime-entry.js')return entry},configureServer(server){server.middlewares.use('/__preview-ime',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__preview-ime',html))}catch(error){next(error)}})}}]}})
