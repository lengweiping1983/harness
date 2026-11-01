import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {mkdtempSync,rmSync} from 'node:fs'
import {readFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {execFileSync} from 'node:child_process'
import {BrowserDownloads} from '../../desktop/downloads.mjs'
import {fixturePython} from './python-fixture.mjs'
import {browserScenario} from './browser-scenario.mjs'

const schedule=JSON.parse(execFileSync(fixturePython,['-c',`import json
from harness.app.schedules import validate_schedule
print(json.dumps(validate_schedule({'schedule':{'kind':'weekly','time':'09:30','days':list(range(7))},'timezone':'UTC'})[0]))`],{encoding:'utf8'}))
const directory=mkdtempSync(path.join(tmpdir(),'harness-dynamic-download-'))
let snapshots
try {
 const producer=new BrowserDownloads(directory)
 for(const status of ['progressing','paused','interrupted','completed','cancelled']){
  const item=new EventEmitter();let paused=false
  Object.assign(item,{getFilename:()=>status+' 原文.bin',getInitiatorOrigin:()=> 'https://fixture.invalid',getTotalBytes:()=>128,getReceivedBytes:()=>32,isPaused:()=>paused,canResume:()=>status==='interrupted',setSavePath:()=>{},pause:()=>{paused=true;item.emit('updated',{},'progressing')},cancel:()=>item.emit('done',{},'cancelled')})
  const id=producer.add(item,'owner')
  if(status==='paused')producer.action('owner',id,'pause')
  else if(['completed','cancelled'].includes(status))item.emit('done',{},status)
  else if(status==='interrupted')item.emit('updated',{},'interrupted')
 }
 snapshots=producer.snapshot('owner')
 assert.deepEqual(producer.snapshot('other'),[])
 producer.close()
}finally{rmSync(directory,{recursive:true,force:true})}
const catalogs=Object.fromEntries(await Promise.all(['en','zh-CN'].map(async locale=>[locale,JSON.parse(await readFile(new URL('../../harness/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))])))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {NativeBrowserPanel} from '/src/components/inspector/NativeBrowserPanel.tsx'
import {AutomationEditor} from '/src/components/overlays/AutomationsPage.tsx'
import {scheduleLabel} from '/src/lib/automations.ts'
import {setLocale,useLocale} from '/src/i18n/index.ts'
import {useAppStore} from '/src/store/app.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.calls=[];window.writes=0
const state={session_id:'owner',active_tab:null,tabs:[],downloads:${JSON.stringify(snapshots)}}
const subscribe=()=>()=>{}
window.harnessDesktop={onFocusFind:subscribe,onFocusAddress:subscribe,onBrowserState:subscribe,onBrowserError:subscribe,browser:async(action,args)=>{window.calls.push({action,args});return state}}
useAppStore.setState({activeSessionId:'owner',sessions:[{id:'owner',title:'Original conversation'}]})
const schedule=${JSON.stringify(schedule)}
function Schedule(){useLocale();return React.createElement('p',{id:'schedule-label'},scheduleLabel(schedule))}
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,
 React.createElement(NativeBrowserPanel,{sessionId:'owner'}),React.createElement(Schedule),
 React.createElement(AutomationEditor,{task:{id:'fixture',name:'Original name 原文',prompt:'Original prompt 原文',session_id:'owner',mode:'fresh',schedule,timezone:'UTC',due:1800000000,enabled:1},initial:{name:'',prompt:''},sessionId:'owner',busy:false,error:'',onCancel:()=>{},onSave:async()=>{window.writes++}})))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 let reads=0
 await page.route('**/api/v1/**',route=>{assert.equal(route.request().method(),'POST');assert.match(route.request().url(),/automations\/preview/);reads++;return route.fulfill({json:{occurrences:[1800000000]}})})
 const previewReady=page.waitForResponse(response=>response.url().includes('/automations/preview'))
 await page.goto(server.resolvedUrls.local[0]+'__dynamic-families')
 await page.clock.runFor(1000);await page.waitForFunction(()=>window.calls.length>0)
 await previewReady
 await page.clock.runFor(100)
 const calls=await page.evaluate(()=>window.calls.filter(call=>call.action!=='layout'))
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const catalog=catalogs[locale]
  for(const row of snapshots){
   const status=row.paused?'paused':row.state
   await page.getByRole('group',{name:row.filename,exact:true}).locator('p').filter({hasText:catalog['browser.desktop.downloads.'+status]}).waitFor()
  }
  const days=['mon','tue','wed','thu','fri','sat','sun'].map(day=>catalog['ui.automations.weekday.'+day])
  for(const day of days)await page.getByRole('button',{name:catalog['ui.automations.weekdayAccessible'].replace('{day}',day),exact:true}).waitFor()
  const expected=catalog['ui.lib.automations.d47891fbea69'].replace('{p0}',days.join(catalog['ui.common.list_separator'])).replace('{p1}','09:30')
  assert.equal(await page.locator('#schedule-label').innerText(),expected)
  assert.deepEqual(await page.evaluate(()=>window.calls.filter(call=>call.action!=='layout')),calls)
  assert.equal(await page.evaluate(()=>window.writes),0);assert.equal(reads,1)
  assert.equal(await page.locator('textarea').inputValue(),'Original prompt 原文')
 }
 assert.deepEqual(errors,[])
 console.log('Producer -> real panel/editor: five download states and seven weekdays, en/zh/en themes/widths; no download action, save or repeated schedule preview')
},{entry:'/__dynamic-families-entry.js',vite:{plugins:[{name:'dynamic-families-fixture',enforce:'pre',resolveId(id){if(id==='/__dynamic-families-entry.js')return id},async load(id){if(id.endsWith('/AutomationsPage.tsx'))return await readFile(id,'utf8')+'\nexport {AutomationEditor}\n';if(id==='/__dynamic-families-entry.js')return entry},configureServer(server){server.middlewares.use('/__dynamic-families',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__dynamic-families','<!doctype html><html><body><div id="root"></div><script type="module" src="/__dynamic-families-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
