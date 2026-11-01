import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ProjectSidebar} from '/src/components/sidebar/ProjectSidebar.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import {validateRefreshBoundaryAndEnqueueUpdate} from '/@react-refresh'
import '/src/styles.css'
window.copy=t;window.locale=setLocale
window.checkRefresh=async()=>{
 const paths=['/src/components/chat/SessionRunningIndicator.tsx','/src/components/chat/ModelPicker.tsx','/src/components/chat/blocks/TeamIntegration.tsx','/src/components/inspector/TerminalTypeSelect.tsx']
 const results=[]
 for(const path of paths){
  const before=await import(/* @vite-ignore */ path)
  const after=await import(/* @vite-ignore */ path+'?refresh-check=1')
  results.push({path,error:validateRefreshBoundaryAndEnqueueUpdate(path,{...before},{...after})??null})
 }
 return results
}
window.loading=(loading,rows=[])=>useAppStore.setState({sessionsLoading:loading,workspacesLoading:false,sessions:rows,workspaces:[]})
window.loading(true)
createRoot(document.getElementById('root')).render(React.createElement(ProjectSidebar))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__refresh-loading')
 await page.waitForFunction(()=>typeof window.checkRefresh==='function')
 const results=await page.evaluate(()=>window.checkRefresh())
 assert.equal(results.length,4)
 for(const row of results)assert.equal(row.error,null,row.path)
 for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({language,theme})=>{window.locale(language);document.documentElement.className=theme;window.loading(true)},{language,theme})
  const label=await page.evaluate(()=>window.copy('ui.sidebar.loading'))
  await page.getByRole('status',{name:label,exact:true}).waitFor()
  assert.equal(await page.getByText(await page.evaluate(()=>window.copy('ui.components.sidebar.ProjectSidebar.6c5e17bc9823')),{exact:true}).count(),0)
  await page.evaluate(()=>window.loading(false,[{id:'fixture',title:'Existing conversation 原文',created_at:1,metadata:{}}]))
  await page.getByRole('button',{name:'Existing conversation 原文',exact:true}).waitFor()
  await page.evaluate(()=>window.loading(true,[{id:'fixture',title:'Existing conversation 原文',created_at:1,metadata:{}}]))
  assert.equal(await page.getByRole('status',{name:label,exact:true}).count(),0)
  await page.getByRole('button',{name:'Existing conversation 原文',exact:true}).waitFor()
  await page.evaluate(()=>window.loading(false))
  await page.getByText(await page.evaluate(()=>window.copy('ui.components.sidebar.ProjectSidebar.6c5e17bc9823')),{exact:true}).waitFor()
 }
 assert.deepEqual(errors,[]);await page.close()
 console.log('Four real refresh boundaries accept changed exports; sidebar loading, refresh preservation and empty state pass the language/theme/width matrix')
},{entry:'/__refresh-loading-entry.js',vite:{plugins:[{name:'refresh-loading',resolveId(id){if(id==='/__refresh-loading-entry.js')return id},load(id){if(id==='/__refresh-loading-entry.js')return entry},configureServer(server){server.middlewares.use('/__refresh-loading',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__refresh-loading','<html><body><div id="root" style="height:900px"></div><script type="module" src="/__refresh-loading-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
