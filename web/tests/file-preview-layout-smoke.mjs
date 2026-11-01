import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {FilePreview} from '/src/components/inspector/FilePreview.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
function Fixture(){const [path,setPath]=useState('fixture.txt');window.file=setPath;window.locale=setLocale;return React.createElement('div',{className:'files-content-pane',style:{height:650}},React.createElement('div',{className:'file-tabs-panel'},React.createElement(FilePreview,{key:path,path,root:'/fixture',focusContent:false})))}
setLocale('en');createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
const html='<html><body><div id="root"></div><script type="module" src="/__file-layout-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage()
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/file?*',route=>new URL(route.request().url()).searchParams.get('path')?.endsWith('.png')
  ?route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"/>'})
  :route.fulfill({contentType:'text/plain',body:Array.from({length:80},(_,i)=>'Original line '+i).join('\n')}))
 await page.goto(server.resolvedUrls.local[0]+'__file-layout')
 for(const width of [390,1440])for(const dark of [false,true])for(const language of ['en','zh-CN','en']){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({dark,language})=>{document.documentElement.classList.toggle('dark',dark);window.locale(language);window.file('fixture.txt')},{dark,language})
  await page.locator('[data-file-source]').waitFor()
  const source=await page.locator('.file-preview-viewport').evaluate(element=>({max:getComputedStyle(element).maxHeight,height:element.clientHeight,scroll:element.scrollHeight}))
  assert.equal(source.max,'none');assert.ok(source.height>480);assert.ok(source.scroll>source.height)
  const frame=await page.locator('.file-preview').evaluate(element=>({display:getComputedStyle(element).display,border:getComputedStyle(element).borderTopWidth,label:element.getAttribute('aria-label')}))
  assert.equal(frame.display,'flex');assert.equal(frame.border,'0px');assert.equal(frame.label,language==='en'?'File preview':'文件预览')
  await page.evaluate(()=>window.file('fixture.png'))
  await page.locator('.file-preview-image img').waitFor()
  assert.equal(await page.locator('.file-preview-image img').evaluate(element=>getComputedStyle(element).maxHeight),'630px')
 }
 assert.deepEqual(errors,[])
 console.log('File preview: text and image layout independent of repeated language switches across themes and widths passed')
},{entry:'/__file-layout-entry.js',vite:{plugins:[{name:'file-layout-fixture',resolveId(id){if(id==='/__file-layout-entry.js')return id},load(id){if(id==='/__file-layout-entry.js')return entry},configureServer(server){server.middlewares.use('/__file-layout',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__file-layout',html))}catch(error){next(error)}})}}]}})
