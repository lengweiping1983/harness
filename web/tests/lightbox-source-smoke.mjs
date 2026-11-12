import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ImageLightbox} from '/src/components/markdown/ImageLightbox.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.openImage=name=>useAppStore.getState().openLightbox('/'+name+'.svg',name)
createRoot(document.getElementById('root')).render(React.createElement(ImageLightbox))
`
await browserScenario(async({server,browser})=>{
 for(const language of ['en','zh-CN']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.route('**/*.svg',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="'+(route.request().url().includes('second')?20:10)+'" height="30"/>'}))
  await page.goto(server.resolvedUrls.local[0]+'__lightbox-source');await page.evaluate(language=>{window.locale(language);window.openImage('first')},language)
  await page.getByText('10 × 30',{exact:true}).waitFor()
  await page.evaluate(()=>{window.oldImage=document.querySelector('[role="dialog"] img');window.openImage('second')})
  await page.getByText('20 × 30',{exact:true}).waitFor()
  await page.evaluate(()=>{Object.defineProperty(window.oldImage,'naturalWidth',{value:999,configurable:true});window.oldImage.dispatchEvent(new Event('load',{bubbles:true}))})
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(resolve)))
  assert.equal(await page.getByText('20 × 30',{exact:true}).count(),1)
  assert.equal(await page.getByText('999 × 30',{exact:true}).count(),0)
  await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'detached'})
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Lightbox source replacement rejects obsolete image load dimensions in both languages')
},{entry:'/__lightbox-source-entry.js',vite:{plugins:[{name:'lightbox-source-fixture',resolveId(id){if(id==='/__lightbox-source-entry.js')return id},load(id){if(id==='/__lightbox-source-entry.js')return entry},configureServer(server){server.middlewares.use('/__lightbox-source',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__lightbox-source','<html><body><div id="root"></div><script type="module" src="/__lightbox-source-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
