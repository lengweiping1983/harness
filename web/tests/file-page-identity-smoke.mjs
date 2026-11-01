import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {FilesTab} from '/src/components/inspector/Inspector.tsx'
import {api} from '/src/lib/api.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.copy=t;window.locale=setLocale;window.reads=0;window.field='root'
const page={root:'/fixture',path:'',parents:[],parent:'',exists:true,truncated:true,is_dir:true,query:'',entries:[{name:'original.txt',path:'original.txt',type:'file'}],next_offset:1}
api.workspace=async(_path,_root,options)=>{window.reads++;return {ok:true,data:options?.offset?{...page,entries:[{name:'foreign.txt',path:'foreign.txt',type:'file'}],next_offset:null,[window.field]:'foreign'}:page}}
useAppStore.setState({activeSessionId:'fixture',workspace:{root:'/fixture'},workspaces:[],sessions:[]})
const root=createRoot(document.getElementById('root'));window.show=active=>root.render(React.createElement(FilesTab,{active}));window.show(true)
`
await browserScenario(async({server,browser})=>{
 for(const field of ['root','path','query']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)})
  await page.goto(server.resolvedUrls.local[0]+'__file-page-identity')
  await page.evaluate(field=>{window.field=field},field)
  await page.getByText('original.txt',{exact:true}).waitFor().catch(async error=>{console.error(await page.locator('body').innerText(), await page.evaluate(()=>window.reads));throw error})
  if(field==='root'){
   const refresh=page.locator('.files-tree-pane button.refresh-action')
   await page.setViewportSize({width:1440,height:900})
   assert.notEqual(await refresh.locator('.refresh-action-label').evaluate(node=>getComputedStyle(node).display),'none','Wide file tree shows Refresh')
   await page.setViewportSize({width:300,height:900})
   assert.equal(await refresh.locator('.refresh-action-label').evaluate(node=>getComputedStyle(node).display),'none','Narrow file tree keeps the icon only')
  }
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.showMore')),exact:true}).click()
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
   await page.getByText(await page.evaluate(()=>window.copy('ui.http.invalidResponse')),{exact:true}).waitFor()
   assert.equal(await page.getByText('foreign.txt',{exact:true}).count(),0)
   await page.getByText('original.txt',{exact:true}).waitFor()
   assert.equal(await page.evaluate(()=>window.reads),2)
  }
  await page.evaluate(()=>window.show(false))
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
  await page.evaluate(()=>window.show(true))
  await page.getByText('original.txt',{exact:true}).waitFor()
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
  assert.equal(await page.evaluate(()=>window.reads),2,'restoring the same root retains its confirmed directory without refetching')
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('File pagination: foreign root/path/query receipts show a live bilingual error and preserve original entries without retry')
},{entry:'/__file-page-identity-entry.js',vite:{plugins:[{
 name:'file-page-identity-fixture',resolveId(id){if(id==='/__file-page-identity-entry.js')return id},load(id){if(id==='/__file-page-identity-entry.js')return entry},
 transform(source,id){if(id.endsWith('/components/inspector/Inspector.tsx'))return source+'\nexport {FilesTab}\n'},
 configureServer(server){server.middlewares.use('/__file-page-identity',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__file-page-identity','<html><body><div id="root"></div><script type="module" src="/__file-page-identity-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
