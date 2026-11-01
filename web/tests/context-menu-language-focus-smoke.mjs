import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ContextActionMenu} from '/src/components/ui/ContextActionMenu.tsx'
import {setLocale,t,useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.actions=[]
const origin=document.getElementById('origin')
const anchor={id:'fixture',x:30,y:50,origin}
function App(){useLocale();return React.createElement(ContextActionMenu,{anchor,label:'Fixture menu',onClose:()=>{},items:[
{id:'copy',label:t('ui.imageActions.copy'),icon:null,run:()=>window.actions.push('copy')},
{id:'save',label:t('ui.imageActions.save'),icon:null,run:()=>window.actions.push('save')}]})}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__context-focus')
 const items=page.getByRole('menuitem');await items.first().waitFor()
 await items.nth(1).focus()
 await page.evaluate(()=>window.saved=document.activeElement)
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  await page.waitForFunction(locale=>document.querySelectorAll('[role="menuitem"]')[1]?.textContent===(locale==='en'?'Save as…':'另存为…'),locale)
  assert.equal(await page.evaluate(()=>document.activeElement===window.saved && window.saved.isConnected),true)
 }
 await page.keyboard.press('Enter')
 assert.deepEqual(await page.evaluate(()=>window.actions),['save'])
 assert.deepEqual(errors,[]);await page.close()
 console.log('Context menu language switch preserves the focused action node and executes it once')
},{entry:'/__context-focus-entry.js',vite:{plugins:[{name:'context-focus',resolveId(id){if(id==='/__context-focus-entry.js')return id},load(id){if(id==='/__context-focus-entry.js')return entry},configureServer(server){server.middlewares.use('/__context-focus',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__context-focus','<html><head><link rel="icon" href="data:,"></head><body><button id="origin">Origin</button><div id="root"></div><script type="module" src="/__context-focus-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
