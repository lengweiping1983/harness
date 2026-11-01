import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {PermissionMenu} from '/src/components/chat/PermissionMenu.tsx'
import {usePermissionStore} from '/src/store/permissions.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.copy=t;window.locale=setLocale;window.saves=0
useAppStore.setState({activeSessionId:'A'})
usePermissionStore.setState({sessionId:'A',scope:'A',policy:'request_approval',loading:false,busy:false,readError:null,saveError:null,load:async()=>{},save:async()=>{window.saves++;await new Promise(resolve=>window.releaseSave=resolve);return true}})
function Fixture(){const [visible,setVisible]=useState(true);window.hide=()=>setVisible(false);return React.createElement(React.Fragment,null,React.createElement('input',{'aria-label':'Other input'}),visible?React.createElement(PermissionMenu,{connection:'open'}):null)}
window.switchSession=()=>useAppStore.setState({activeSessionId:'B'})
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const language of ['en','zh-CN'])for(const mode of ['normal','closed','reopen','switch','unmount']){
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__model-width')
 await page.evaluate(language=>window.locale(language),language)
 const trigger=page.locator('button[aria-haspopup="menu"]')
 await trigger.click()
 await page.getByRole('menuitemradio').last().click()
 await page.waitForFunction(()=>Boolean(window.releaseSave))
 if(mode==='unmount'){await page.evaluate(()=>window.hide());await trigger.waitFor({state:'detached'})}
 if(mode==='switch'){await page.evaluate(()=>window.switchSession());await page.getByRole('menu').waitFor({state:'detached'})}
 if(mode==='closed'||mode==='reopen')await page.getByRole('textbox',{name:'Other input'}).click()
 if(mode==='reopen')await trigger.click()
 if(mode!=='normal'&&mode!=='reopen')await page.getByRole('textbox',{name:'Other input'}).focus()
 const before=await page.evaluate(()=>document.activeElement?.outerHTML)
 await page.evaluate(async()=>{window.releaseSave();await Promise.resolve();await Promise.resolve()})
 if(mode==='normal'){
  await page.getByRole('menu').waitFor({state:'detached'})
  assert.equal(await trigger.evaluate(node=>node===document.activeElement),true)
 }else{
  assert.equal(await page.evaluate(()=>document.activeElement?.outerHTML),before)
  if(mode==='reopen')assert.equal(await page.getByRole('menu').count(),1)
 }
 assert.equal(await page.evaluate(()=>window.saves),1)
 assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Permission save receipts preserve focus and replacement menu ownership across close, reopen, session switch and unmount')
},{entry:'/__model-width-entry.js',vite:{plugins:[{name:'model-width',resolveId(id){if(id==='/__model-width-entry.js')return id},load(id){if(id==='/__model-width-entry.js')return entry},configureServer(server){server.middlewares.use('/__model-width',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__model-width','<html><body><div id="root"></div><script type="module" src="/__model-width-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
