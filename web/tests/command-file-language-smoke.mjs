import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {CommandPalette} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
useAppStore.setState({commandOpen:true,workspaces:[{id:'fixture',path:'/fixture',name:'Original'}],activeWorkspaceId:'fixture',sessions:[]})
createRoot(document.getElementById('root')).render(React.createElement(CommandPalette))
`
await browserScenario(async({server,browser})=>{
 for(const kind of ['mismatch','unavailable','malformed']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/workspace?*',route=>{
   requests.push(route.request().method())
   return kind==='malformed'?route.fulfill({status:200,contentType:'application/json',body:'broken'}):route.fulfill({json:{root:kind==='mismatch'?'/foreign':'/fixture',path:'',parents:[],parent:'',exists:kind!=='unavailable',is_dir:kind!=='unavailable',truncated:false,entries:[]}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__command-file-language')
  const key=kind==='mismatch'?'ui.components.overlays.CommandPalette.cbf19838304f':kind==='unavailable'?'ui.components.overlays.CommandPalette.3177e3d2fabb':'ui.http.invalidResponse'
  const expected=()=>page.evaluate(key=>window.copy(key),key)
  await page.getByRole('alert').getByText(await expected(),{exact:true}).waitFor()
  await page.getByRole('combobox').fill('Original search 原文')
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
   await page.getByRole('alert').getByText(await expected(),{exact:true}).waitFor()
   assert.equal(await page.getByRole('combobox').inputValue(),'Original search 原文')
   assert.deepEqual(requests,['GET'],'language changes must not repeat directory reads')
  }
  assert.deepEqual(errors,[]);await page.close();console.log(kind+': directory failure retains resource identity, query and single read across language/theme/width changes')
 }
},{entry:'/__command-file-language-entry.js',vite:{plugins:[{
 name:'command-file-language-fixture',resolveId(id){if(id==='/__command-file-language-entry.js')return id},load(id){if(id==='/__command-file-language-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__command-file-language',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__command-file-language','<html><body><div id="root"></div><script type="module" src="/__command-file-language-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
