import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ScreenshotButton} from '/src/components/chat/ScreenshotButton.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {api} from '/src/lib/api.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.captures=0;window.effects=[];window.switchTo=id=>useAppStore.setState({activeSessionId:id})
api.screenshotCapabilities=async()=>({ok:true,data:{native:true,hideWindow:true,reason:''}})
api.screenshot=async()=>{window.captures++;if(window.hold)await new Promise((resolve,reject)=>{window.rejectCapture=reject});return {ok:false,status:200,code:'response/invalid-json',message:'Old snapshot',copy:{key:'ui.http.invalidResponse'}}}
function Fixture(){const [show,setShow]=useState(true);window.show=setShow;window.clear=()=>useAppStore.setState({toasts:[]});return React.createElement(React.Fragment,null,show?React.createElement(ScreenshotButton,{addFiles:async()=>window.effects.push('add'),focusEditor:()=>window.effects.push('focus')}):null,React.createElement(Toaster))}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 await page.goto(server.resolvedUrls.local[0]+'__screenshot-error')
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.ScreenshotButton.c95dc99afe57')),exact:true}).click()
 await page.getByText(await page.evaluate(()=>window.copy('ui.http.invalidResponse')),{exact:true}).waitFor()
 for(const locale of ['en','zh-CN','en']) for(const theme of ['light','dark']) for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.dataset.theme=theme},{locale,theme})
  const expected=await page.evaluate(()=>window.copy('ui.http.invalidResponse'))
  await page.getByText(expected,{exact:true}).waitFor();assert.equal(await page.evaluate(()=>window.captures),1)
 }
 for(const roundtrip of [false,true]){
  await page.evaluate(()=>{window.clear();window.effects=[];window.hold=true;window.rejectCapture=null;window.switchTo('A')})
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.ScreenshotButton.c95dc99afe57')),exact:true}).click()
  await page.waitForFunction(()=>Boolean(window.rejectCapture))
  await page.evaluate(roundtrip=>{window.switchTo('B');if(roundtrip)window.switchTo('A');window.rejectCapture(new Error('Original other conversation'));},roundtrip)
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.ScreenshotButton.c95dc99afe57')),exact:true}).waitFor()
  assert.deepEqual(await page.evaluate(()=>window.effects),[])
  assert.equal(await page.getByText('Original other conversation',{exact:true}).count(),0)
 }
 await page.evaluate(()=>{window.clear();window.effects=[];window.hold=true;window.locale('en')})
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.ScreenshotButton.c95dc99afe57')),exact:true}).click()
 await page.waitForFunction(()=>Boolean(window.rejectCapture))
 await page.evaluate(()=>window.show(false))
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.chat.ScreenshotButton.aca98bb4fefc')),exact:true}).waitFor({state:'detached'})
 await page.evaluate(async()=>{window.rejectCapture(new Error('Original late failure'));await Promise.resolve();await Promise.resolve()})
 assert.equal(await page.getByText('Original late failure',{exact:true}).count(),0)
 assert.deepEqual(await page.evaluate(()=>window.effects),[])
 assert.deepEqual(errors,[]);await page.close();console.log('Screenshot button: failure resources follow language without repeating capture')

},{entry:'/__screenshot-error-entry.js',vite:{plugins:[{name:'screenshot-error-language-fixture',resolveId(id){if(id==='/__screenshot-error-entry.js')return id},load(id){if(id==='/__screenshot-error-entry.js')return entry},configureServer(server){server.middlewares.use('/__screenshot-error',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__screenshot-error','<html><body><div id="root"></div><script type="module" src="/__screenshot-error-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
