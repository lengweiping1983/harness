import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ScreenshotButton} from '/src/components/chat/ScreenshotButton.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.effects=[]
useAppStore.setState({toasts:[]})
window.toasts=()=>useAppStore.getState().toasts
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,
React.createElement(ScreenshotButton,{addFiles:async()=>window.effects.push('add'),focusEditor:()=>window.effects.push('focus')}),React.createElement(Toaster)))
`
await browserScenario(async({server,browser})=>{
 for(const failed of [false,true]){
  const page=await browser.newPage(),requests=[],errors=[]
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request();requests.push({method:request.method(),path:new URL(request.url()).pathname})
   return route.fulfill(failed?{status:503,json:{code:'fixture/offline',error:'Original diagnostic'}}:
    {json:{native:false,hideWindow:false,reason:{key:'ui.screenshot.nativeUnavailable'}}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__screenshot-capability')
  const reasonKey=failed?'ui.components.chat.ScreenshotButton.a0db5328de97':'ui.screenshot.nativeUnavailable'
  await page.waitForFunction(()=>typeof window.copy==='function')
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.ScreenshotButton.baff2e55a8d0')),exact:true}).click()
  await page.getByText(await page.evaluate(key=>window.copy(key),reasonKey),{exact:true}).waitFor()
  await page.getByRole('menuitem').click({force:true})
  await page.waitForFunction(()=>window.toasts().length===1)
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   const texts=await page.evaluate(key=>[window.copy(key),window.copy('ui.components.chat.ScreenshotButton.e3dd73a43278')],reasonKey)
   await page.waitForFunction(texts=>texts.every(text=>document.body.textContent.includes(text)),texts)
   const reason=page.getByText(texts[0],{exact:true});assert.equal(await reason.count(),2)
   assert.deepEqual(await page.evaluate(()=>window.effects),[])
   assert.equal(await page.evaluate(()=>window.toasts().length),1)
   assert.deepEqual(requests,[{method:'GET',path:'/api/v1/screenshot/capabilities'}])
  }
  assert.deepEqual(errors,[]);await page.close();console.log('Screenshot capability '+(failed?'read failure':'unavailable')+': menu and existing toast follow locale; one read, no capture')
 }
},{entry:'/__screenshot-capability-entry.js',vite:{plugins:[{
 name:'screenshot-capability-language-fixture',resolveId(id){if(id==='/__screenshot-capability-entry.js')return id},load(id){if(id==='/__screenshot-capability-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__screenshot-capability',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__screenshot-capability','<html><body><div id="root"></div><script type="module" src="/__screenshot-capability-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
