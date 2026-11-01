import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
sessionStorage.setItem('harness.git-drafts.v1','Original damaged fixture')
const {GitStaging}=await import('/src/components/overlays/GitStaging.tsx')
setLocale('en');window.locale=setLocale;window.copy=t
const repository={root:'/fixture',branch:'main',refs:[],head:null,index_revision:'fixture',conflicts:false,staged_files:[],files:[]}
createRoot(document.getElementById('root')).render(React.createElement(GitStaging,{sessionId:'owner',repository,busy:false,onBusy:()=>{},onUpdate:()=>{}}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message));page.on('request',req=>{if(req.url().includes('/api/v1/'))requests.push(req.url())})
 await page.goto(server.resolvedUrls.local[0]+'__git-draft-integrity')
 await page.getByRole('textbox').fill('New commit draft 原文')
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
  await page.getByRole('alert').getByText(await page.evaluate(()=>window.copy('ui.git.draftCorrupt')),{exact:true}).waitFor()
  assert.equal(await page.getByRole('textbox').inputValue(),'New commit draft 原文')
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('harness.git-drafts.v1')),'Original damaged fixture')
  assert.equal(await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.git.retryDraft')),exact:true}).count(),0)
 }
 assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);await page.close();console.log('Git draft corruption: original preserved, new text retained, bilingual feedback and no misleading retry')
},{entry:'/__git-draft-integrity-entry.js',vite:{plugins:[{
 name:'git-draft-integrity-fixture',resolveId(id){if(id==='/__git-draft-integrity-entry.js')return id},load(id){if(id==='/__git-draft-integrity-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__git-draft-integrity',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__git-draft-integrity','<html><body><div id="root"></div><script type="module" src="/__git-draft-integrity-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
