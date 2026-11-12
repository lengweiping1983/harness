import assert from 'node:assert/strict'
import {WebsitePermissions} from '../../desktop/permissions.mjs'
import {browserScenario} from './browser-scenario.mjs'

const origin='https://fixture.invalid'
const pending=new WebsitePermissions(),granted=new WebsitePermissions()
for(const permission of ['notifications','geolocation','geolocation-approximate','clipboard-read','clipboard-sanitized-write','media']){
 const details={requestingUrl:origin,mediaTypes:['audio','video']}
 pending.request('tab',origin,permission,()=>{},details)
 granted.request('tab',origin,permission,()=>{},details)
}
for(const request of granted.snapshot('tab',origin).requests)granted.answer('tab',origin,request.id,true)
const snapshot={...pending.snapshot('tab',origin),grants:granted.snapshot('tab',origin).grants}
assert.deepEqual(snapshot.grants,['notifications','geolocation','geolocation-approximate','clipboard-read','clipboard-sanitized-write','microphone','camera'])
assert.equal(snapshot.requests.length,6)
pending.clear('tab');granted.clear('tab')
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {NativeBrowserPanel} from '/src/components/inspector/NativeBrowserPanel.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;setLocale('en');window.calls=[]
const state={session_id:'owner',active_tab:null,tabs:[],permissions:${JSON.stringify(snapshot)}}
const subscribe=()=>()=>{}
window.harnessDesktop={onFocusFind:subscribe,onFocusAddress:subscribe,onBrowserState:subscribe,onBrowserError:subscribe,browser:async(action,args)=>{window.calls.push({action,args});return state}}
createRoot(document.getElementById('root')).render(React.createElement(NativeBrowserPanel,{sessionId:'owner'}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__native-permissions')
 const english=['Notifications','Precise location','Approximate location','Read clipboard','Write clipboard','Microphone','Camera']
 const chinese=['通知','精确位置','大致位置','读取剪贴板','写入剪贴板','麦克风','摄像头']
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const labels=locale==='en'?english:chinese,separator=locale==='en'?', ':'、'
  await page.getByText(labels.join(separator),{exact:true}).waitFor()
  for(const request of snapshot.requests){
   const names=request.capabilities.map(name=>labels[snapshot.grants.indexOf(name)]).join(separator)
   await page.getByText((locale==='en'?'Requests access to: ':'请求访问：')+names,{exact:true}).waitFor()
  }
  assert.equal((await page.evaluate(()=>window.calls)).filter(call=>['permission_answer','permission_reset'].includes(call.action)).length,0)
 }
 const region=page.getByRole('region',{name:'Website permission request',exact:true}).last()
 await region.getByRole('button',{name:'Allow for this page',exact:true}).click()
 const writes=(await page.evaluate(()=>window.calls)).filter(call=>call.action==='permission_answer')
 assert.equal(writes.length,1)
 assert.deepEqual(writes[0].args,{session_id:'owner',id:snapshot.requests.at(-1).id,allow:true})
 assert.deepEqual(errors,[])
 console.log('Desktop permission producer -> native panel: all 7 dynamic capability keys, localized separators, en/zh/en themes/widths, explicit single approval identity passed')
},{entry:'/__native-permissions-entry.js',vite:{plugins:[{name:'native-permissions-fixture',resolveId(id){if(id==='/__native-permissions-entry.js')return id},load(id){if(id==='/__native-permissions-entry.js')return entry},configureServer(server){server.middlewares.use('/__native-permissions',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__native-permissions','<html><body><div id="root"></div><script type="module" src="/__native-permissions-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
