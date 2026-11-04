import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {websiteMenu} from '../../desktop/context-menu.mjs'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {useDesktopMenu} from '/src/hooks/useDesktopMenu.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
window.locale=setLocale;setLocale('en');window.menus=[];window.commands=[]
window.harnessDesktop={setMenu:async labels=>{if(window.failMenu)throw Error('Original menu diagnostic 原文 {key}');window.menus.push(labels)},onCommand:()=>()=>{}}
useAppStore.setState({booted:true})
function Fixture(){useDesktopMenu();return React.createElement('p',null,'Ready')}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[],diagnostics=[];page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')diagnostics.push(message.text())})
 await page.goto(server.resolvedUrls.local[0]+'__desktop-menu')
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  const expected=locale==='en'?['Open link in new tab','Copy link address','Inspect element']:['在新标签打开链接','复制链接地址','检查元素']
  await page.waitForFunction(expected=>window.menus.at(-1)?.contextOpenLink===expected,expected[0])
  const labels=await page.evaluate(()=>window.menus.at(-1))
  const resources=JSON.parse(readFileSync(new URL('../../harness/i18n/locales/'+locale+'.json',import.meta.url)))
  const expectedLabels=Object.fromEntries(Object.entries(resources).filter(([key])=>key.startsWith('ui.desktop.menu.')).map(([key,value])=>[key.slice('ui.desktop.menu.'.length),value]))
  assert.equal(Object.keys(labels).length,Object.keys(expectedLabels).length)
  assert.equal(Object.hasOwn(labels,'close'),false)
  assert.deepEqual(labels,expectedLabels)
  assert.deepEqual([labels.contextOpenLink,labels.contextCopyLink,labels.contextInspect],expected)
  const actions=[]
  const items=websiteMenu({linkURL:'https://fixture.invalid/原文',selectionText:'Original',editFlags:{canCopy:true},x:1,y:2},{open:url=>actions.push(url),copyLink:url=>actions.push(url),edit:name=>actions.push(name),inspect:()=>actions.push('inspect')},key=>labels[key])
  assert.deepEqual(items.filter(item=>item.id).map(item=>item.label),[expected[0],expected[1],labels.copy,expected[2]])
  assert.deepEqual(actions,[])
 }
 await page.evaluate(()=>{window.failMenu=true})
 for(const locale of ['zh-CN','en','zh-CN']){
  const count=diagnostics.length
  const observed=page.waitForEvent('console',{predicate:message=>message.type()==='error'})
  await page.evaluate(locale=>window.locale(locale),locale)
  const resources=JSON.parse(readFileSync(new URL('../../harness/i18n/locales/'+locale+'.json',import.meta.url)))
  await observed
  assert.equal(diagnostics.length,count+1)
  assert.ok(diagnostics.at(-1).startsWith(resources['ui.desktop.menuSyncFailed']))
  assert.ok(diagnostics.at(-1).includes('Original menu diagnostic 原文 {key}'))
 }
 assert.deepEqual(errors,[])
 console.log('Desktop menu hook -> resource payload -> website menu: en/zh/en and shared edit labels; no menu actions on language change')
},{entry:'/__desktop-menu-entry.js',vite:{plugins:[{name:'desktop-menu-language-fixture',resolveId(id){if(id==='/__desktop-menu-entry.js')return id},load(id){if(id==='/__desktop-menu-entry.js')return entry},configureServer(server){server.middlewares.use('/__desktop-menu',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__desktop-menu','<html><body><div id="root"></div><script type="module" src="/__desktop-menu-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
