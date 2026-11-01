import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const entry=`
import React,{useRef} from 'react'
import {createRoot} from 'react-dom/client'
import {TurnElapsed} from '/src/components/chat/blocks/TurnElapsed.tsx'
import {TriggerMenu} from '/src/components/chat/TriggerMenu.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.actions=[]
const cases=['working','unknown','completed','interrupted','stopped','ended'].flatMap((name,index)=>[false,true].map(missing=>({name,missing,activity:{turn:index*2+Number(missing)+1,...(!missing?{startedAt:1000}:{}),...(!['working','unknown'].includes(name)?{endedAt:11000,endSeq:20,reason:name==='stopped'?'aborted':name==='ended'?'error':name}:{} )}})))
window.cases=cases
function Fixture(){const anchor=useRef(null);return React.createElement('main',null,
 ...cases.map(row=>React.createElement('section',{'data-case':row.name+'-'+row.missing,key:row.activity.turn},React.createElement(TurnElapsed,{activity:row.activity,active:row.name==='working'}))),
 React.createElement('button',{ref:anchor,style:{width:320}},'Original anchor'),
 React.createElement(TriggerMenu,{id:'finite-menu',anchor,title:'Original menu 原文',activeIndex:0,items:['directory','file','skill','session','command'].map(kind=>({candidate:{kind,title:'Original '+kind,insert:kind,description:'Original description 原文'},score:1,hits:[]})),onPick:()=>window.actions.push('pick'),onClose:()=>window.actions.push('close')}))}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(11000)});await page.clock.pauseAt(new Date(11000))
 await page.goto(server.resolvedUrls.local[0]+'__finite-timing-menu-resource')
 const keys={directory:'ui.components.overlays.CommandPalette.52daa71ebc31',file:'ui.components.inspector.Inspector.39932f24fe11',skill:'ui.components.chat.TriggerMenu.99aea2f9131a',session:'ui.components.overlays.CommandPalette.a63280253f17',command:'ui.components.chat.TriggerMenu.928f87d4507b'}
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const name of ['working','unknown','completed','interrupted','stopped','ended'])for(const missing of [false,true]){
   const key='ui.turnElapsed.'+(name==='unknown'?'unknown':name+(missing?'Unknown':''));assert.ok(resources[locale][key],key)
   const expected=resources[locale][key].replace('{duration}',resources[locale]['ui.turnElapsed.seconds'].replace('{seconds}','10'))
   const section=page.locator('[data-case='+JSON.stringify(name+'-'+missing)+']')
   await section.getByText(expected,{exact:true}).waitFor()
   assert.equal(await section.locator('[data-timing-active]').getAttribute('data-timing-active'),String(name==='working'))
  }
  for(const [kind,key] of Object.entries(keys)){
   assert.ok(resources[locale][key]);await page.getByRole('listbox',{name:'Original menu 原文',exact:true}).getByText(resources[locale][key]+'1',{exact:true}).waitFor()
   await page.getByRole('option').getByText('Original '+kind,{exact:true}).waitFor()
  }
  assert.deepEqual(await page.evaluate(()=>window.actions),[])
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Finite turn labels and five completion groups: en→zh→en, retained original candidates and no actions')
},{entry:'/__finite-timing-menu-resource-entry.js',vite:{plugins:[{name:'finite-timing-menu-resource',resolveId(id){if(id==='/__finite-timing-menu-resource-entry.js')return id},load(id){if(id==='/__finite-timing-menu-resource-entry.js')return entry},configureServer(server){server.middlewares.use('/__finite-timing-menu-resource',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__finite-timing-menu-resource','<html><body><div id="root"></div><script type="module" src="/__finite-timing-menu-resource-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
