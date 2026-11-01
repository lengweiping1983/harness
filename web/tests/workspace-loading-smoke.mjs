import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {DeferredWorkspace} from '/src/components/inspector/DeferredWorkspace.tsx'
import {DeferredPanel} from '/src/components/ui/DeferredPanel.tsx'
import {loadGitWorkspace} from '/src/components/inspector/panelLoaders.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
const root=createRoot(document.getElementById('root'));window.copy=t
window.show=(kind,active,language)=>{setLocale(language);root.render(kind==='git'?React.createElement(DeferredPanel,{load:loadGitWorkspace,panelProps:{active},label:t('ui.workspace.git.title')}):React.createElement(DeferredWorkspace,{kind,sessionId:null,active}))}
`
await browserScenario(async({server,browser})=>{
 for(const kind of ['terminal','browser','git'])for(const language of ['en','zh-CN']){
  const page=await browser.newPage(),errors=[];let pending,requests=0
  page.on('pageerror',e=>errors.push(e.message));await page.clock.install()
  const module=kind==='terminal'?'inspector/TerminalWorkbench':kind==='browser'?'inspector/BrowserPanel':'overlays/WorktreesPanel'
  await page.route('**/src/components/'+module+'.tsx*',route=>{pending=route;requests++})
  await page.goto(server.resolvedUrls.local[0]+'__workspace-loading')
  await page.waitForFunction(()=>typeof window.show==='function')
  if(kind!=='git'){
   await page.evaluate(args=>window.show(...args),[kind,false,language])
   await page.clock.runFor(10001);assert.equal(requests,0,'Unvisited hidden workspace must not load')
  }
  await page.evaluate(args=>window.show(...args),[kind,true,language])
  await page.getByRole('status').waitFor()
  await page.clock.runFor(10001);await page.getByRole('alert').waitFor()
  assert.equal(requests,1);assert.equal(await page.getByRole('status').count(),0)
  await pending.abort('failed')
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.deferred.reload')),exact:true}).waitFor()
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Terminal/browser/Git: bounded module loading, hidden initial workspaces and bilingual failure recovery passed')
},{entry:'/__workspace-loading-entry.js',vite:{plugins:[{
 name:'workspace-loading-fixture',resolveId(id){if(id==='/__workspace-loading-entry.js')return id},load(id){if(id==='/__workspace-loading-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__workspace-loading',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__workspace-loading','<html><body><div id="root"></div><script type="module" src="/__workspace-loading-entry.js"></script></body></html>'))}catch(e){next(e)}})}
}]}})
