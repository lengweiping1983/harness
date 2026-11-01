import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const catalogs=Object.fromEntries(await Promise.all(['en','zh-CN'].map(async language=>[
 language,JSON.parse(await readFile(new URL('../../harness/i18n/locales/'+language+'.json',import.meta.url)))])))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ConnectorsPage} from '/src/components/overlays/EscPanel.tsx'
import {getLocale,setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.language=getLocale
createRoot(document.getElementById('root')).render(React.createElement(ConnectorsPage))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(10000)
 const errors=[],writes=[];let reads=0
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(request.method()!=='GET'){writes.push(path);return route.fulfill({status:500,json:{error:'Unexpected write'}})}
  if(path.endsWith('/presets'))return route.fulfill({json:{presets:[]}})
  if(path==='/api/v1/plugins/connectors'){
   reads++
   const language=await page.evaluate(()=>window.language())
   const connectors=[['product','[connector/oauth-timeout] '+catalogs[language]['python.oauth.142e1d117565']],
    ['external','Original provider diagnostic 原文 {name}']].map(([name,message])=>({name,transport:'streamable-http',
      url:'https://fixture.invalid/mcp',enabled:true,bound:false,connected:false,tool_count:0,
      oauth:{supported:true,state:'error',message}}))
   return route.fulfill({json:{enabled:true,connectors,environment:[]}})
  }
  throw Error('Unexpected OAuth error read '+path)
 })
 await page.goto(server.resolvedUrls.local[0]+'__oauth-error-language')
 for(const dark of [false,true])for(const width of [390,1440])for(const language of ['en','zh-CN','en']){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({language,dark})=>{window.locale(language);document.documentElement.classList.toggle('dark',dark)},{language,dark})
  const expected='[connector/oauth-timeout] '+catalogs[language]['python.oauth.142e1d117565']
  await page.getByText(expected,{exact:true}).waitFor()
  await page.getByText('Original provider diagnostic 原文 {name}',{exact:true}).waitFor()
  assert.deepEqual(writes,[])
 }
 assert.ok(reads>=3);assert.deepEqual(errors,[]);await page.close()
 console.log('OAuth errors follow refreshed language while external diagnostics remain literal; themes and sizes never trigger authorization writes')
},{entry:'/__oauth-error-language-entry.js',vite:{plugins:[{name:'oauth-error-language-fixture',resolveId(id){if(id==='/__oauth-error-language-entry.js')return id},load(id){if(id==='/__oauth-error-language-entry.js')return entry},configureServer(server){server.middlewares.use('/__oauth-error-language',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__oauth-error-language','<html><body><div id="root"></div><script type="module" src="/__oauth-error-language-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
