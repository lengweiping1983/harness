import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
const nativeFetch=window.fetch.bind(window);window.fetch=(input,options)=>String(input).includes('empty.txt')?Promise.resolve(new Response(null,{status:204})):nativeFetch(input,options)
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {FilePreview} from '/src/components/inspector/FilePreview.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
function Fixture(){const [path,setPath]=useState('fixture.txt');window.file=setPath;window.locale=setLocale;window.label=(key,values)=>t(key,values);return React.createElement(FilePreview,{key:path,path,root:'/fixture',focusContent:false})}
setLocale('en');createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
const html='<html><body><div id="root"></div><script type="module" src="/__file-language-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
 let reads=0
 await page.route('**/api/v1/file?*',route=>{reads++;const path=new URL(route.request().url()).searchParams.get('path');return path==='empty.txt'?route.fulfill({status:204}):path==='network.txt'?route.abort('failed'):path==='failed.txt'?route.fulfill({status:404,body:''}):route.fulfill({contentType:'text/plain',body:'first\nsecond\nthird'})})
 await page.goto(server.resolvedUrls.local[0]+'__file-language');await page.locator('[data-file-source]').waitFor()
 const line=page.locator('#file-line-location');await line.fill('2');await line.press('Enter')
 for(const language of ['en','zh-CN','en']){
  await page.evaluate(value=>window.locale(value),language)
  const expected=await page.evaluate(()=>window.label('ui.components.inspector.FilePreview.cf9dbbb7dd22',{p0:2,p1:''}))
  await page.getByRole('status').filter({hasText:expected}).waitFor({timeout:2000})
  assert.equal(await line.inputValue(),'2')
 }
 await line.fill('999');await line.press('Enter')
 for(const language of ['zh-CN','en']){
  await page.evaluate(value=>window.locale(value),language)
  const expected=await page.evaluate(()=>window.label('ui.components.inspector.FilePreview.f04b52622146',{p0:3,p1:''}))
  assert.equal((await page.getByRole('status').innerText()).trim(),expected.trim())
 }
 const before=reads
 await page.evaluate(()=>window.file('failed.txt'));await page.getByRole('alert').waitFor()
 for(const language of ['en','zh-CN','en']){
  await page.evaluate(value=>window.locale(value),language)
  const expected=await page.evaluate(()=>window.label('ui.components.inspector.FilePreview.d9f885d74720',{p0:404}))
  assert.ok((await page.getByRole('alert').innerText()).includes(expected))
 }
 assert.equal(reads,before+1,'Changing language must not re-read the file')
 for(const [path,selector,key] of [['empty.txt',null,'ui.components.inspector.FilePreview.c9e91b53401f'],['network.txt',null,'ui.filePreview.readFailure'],['image.png','img','ui.components.inspector.FilePreview.effa0053fe92'],['video.mp4','video','ui.components.inspector.FilePreview.fec966685ac8'],['audio.mp3','audio','ui.components.inspector.FilePreview.826521a45354']]){
  await page.evaluate(path=>window.file(path),path)
  if(selector)await page.locator(selector).evaluate(node=>node.dispatchEvent(new Event('error')))
  await page.getByRole('alert').waitFor()
  const originalReads=reads
  for(const language of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({language,dark})=>{window.locale(language);document.documentElement.classList.toggle('dark',dark)},{language,dark})
   const expected=await page.evaluate(key=>window.label(key,{detail:'Failed to fetch'}),key)
   await page.getByRole('alert').getByText(expected,{exact:true}).waitFor()
   assert.equal(reads,originalReads,'Changing language must not reload failed content')
  }
 }
 assert.deepEqual(errors,[])
 console.log('File preview language: existing navigation success, invalid line and HTTP failure update without re-reading or losing input')
},{entry:'/__file-language-entry.js',vite:{plugins:[{name:'file-language-fixture',resolveId(id){if(id==='/__file-language-entry.js')return id},load(id){if(id==='/__file-language-entry.js')return entry},configureServer(server){server.middlewares.use('/__file-language',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__file-language',html))}catch(error){next(error)}})}}]}})
