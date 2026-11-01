import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {WorktreesPanel} from '/src/components/overlays/WorktreesPanel.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.copy=t;window.locale=setLocale
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',cwd:'/fixture'}]})
createRoot(document.getElementById('root')).render(React.createElement(WorktreesPanel))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 let catalog,git
 await page.route('**/api/v1/**',route=>{
  const path=new URL(route.request().url()).pathname
  if(path.endsWith('/worktrees')){catalog=route;return}
  if(path.endsWith('/git')){git=route;return}
  return route.fulfill({json:{}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__git-loading')
 await page.getByRole('status').waitFor()
 await page.waitForFunction(()=>document.querySelector('[aria-busy="true"]')!==null)
 while(!git||!catalog)await page.waitForTimeout(20)
 await git.fulfill({json:{root:'/fixture',branch:'main',refs:['main'],head:'a'.repeat(40),index_revision:'revision',conflicts:false,files:[],staged_files:[]}})
 await page.getByText('/fixture',{exact:true}).waitFor()
 assert.equal(await page.locator('[aria-busy="true"]').count(),1,'Repository must render while worktree catalog remains pending')
 await catalog.fulfill({json:{worktrees:[]}})
 await page.locator('[aria-busy="false"]').waitFor()
 for(const locale of ['en','zh-CN'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
  await page.getByText('/fixture',{exact:true}).waitFor()
  const refresh=page.locator('#root > section.refresh-action-container > div:first-child > button.refresh-action')
  assert.equal(await refresh.count(),1,'Git workspace header uses the shared refresh button')
  assert.equal(await refresh.locator('svg').count(),1)
  assert.equal(await refresh.evaluate(element=>getComputedStyle(element).borderTopWidth),'0px')
  assert.notEqual(await refresh.locator('.refresh-action-label').evaluate(element=>getComputedStyle(element).display),'none')
 }
 await page.locator('#root').evaluate(element=>{element.style.width='280px'})
 const narrowRefresh=page.locator('#root > section.refresh-action-container > div:first-child > button.refresh-action')
 assert.equal(await narrowRefresh.locator('.refresh-action-label').evaluate(element=>getComputedStyle(element).display),'none')
 assert.deepEqual(errors,[])
 await page.close();console.log('Git data skeleton and progressive repository rendering passed')
},{entry:'/__git-loading-entry.js',vite:{plugins:[{
 name:'git-loading-fixture',resolveId(id){if(id==='/__git-loading-entry.js')return id},load(id){if(id==='/__git-loading-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__git-loading',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__git-loading','<html><body><div id="root"></div><script type="module" src="/__git-loading-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
