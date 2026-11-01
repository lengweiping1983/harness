import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ScreenshotButton} from '/src/components/chat/ScreenshotButton.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
window.locale=setLocale;window.copy=t;window.captures=0;window.finishes=0;window.focused=0
window.harnessDesktop={screenshot:async action=>{if(action==='capture'){window.captures++;return null}window.finishes++;return await new Promise((resolve,reject)=>{window.resolveFinish=resolve;window.rejectFinish=reject})}}
api.screenshotCapabilities=async()=>({ok:true,data:{native:true,hideWindow:true,reason:null}})
function Fixture(){const [visible,setVisible]=useState(true);window.hide=()=>setVisible(false);return React.createElement(React.Fragment,null,visible?React.createElement(ScreenshotButton,{addFiles:async()=>{},focusEditor:()=>window.focused++}):null,React.createElement(Toaster))}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['success','failure','unmount']){
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.goto(server.resolvedUrls.local[0]+'__screenshot-finish');await page.evaluate(()=>window.locale('en'))
  const label=()=>page.evaluate(()=>window.copy('ui.components.chat.ScreenshotButton.c95dc99afe57'))
  await page.getByRole('button',{name:await label(),exact:true}).click()
  await page.waitForFunction(()=>window.finishes===1)
  assert.equal(await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.ScreenshotButton.aca98bb4fefc')),exact:true}).isDisabled(),true)
  assert.equal(await page.evaluate(()=>window.focused),0)
  if(mode==='unmount')await page.evaluate(()=>window.hide())
  await page.evaluate(mode=>mode==='success'?window.resolveFinish(null):window.rejectFinish(new Error('Original restore failure')),mode)
  if(mode==='success'){await page.waitForFunction(()=>window.focused===1);assert.equal(await page.getByRole('button',{name:await label(),exact:true}).isEnabled(),true)}
  else for(const language of ['en','zh-CN','en']){
   await page.evaluate(language=>window.locale(language),language)
   await page.getByText(await page.evaluate(()=>window.copy('ui.screenshot.cleanupUnknown')),{exact:true}).waitFor()
   if(mode!=='unmount')assert.equal(await page.getByRole('button',{name:await label(),exact:true}).isDisabled(),true)
   assert.equal(await page.evaluate(()=>window.focused),0)
  }
  assert.equal(await page.evaluate(()=>window.captures),1);assert.equal(await page.evaluate(()=>window.finishes),1)
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Native screenshot finish is awaited once; failure stays visible across languages and never confirms recovery or restores focus')
},{entry:'/__screenshot-finish-entry.js',vite:{plugins:[{name:'screenshot-finish-fixture',resolveId(id){if(id==='/__screenshot-finish-entry.js')return id},load(id){if(id==='/__screenshot-finish-entry.js')return entry},configureServer(server){server.middlewares.use('/__screenshot-finish',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__screenshot-finish','<html><body><div id="root"></div><script type="module" src="/__screenshot-finish-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
