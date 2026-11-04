import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const source=readFileSync(new URL('../src/lib/settingsCatalog.ts',import.meta.url),'utf8')
const pages=[...source.matchAll(/id: '([^']+)', group: '[^']+', labelKey: '([^']+)', keywordsKey: '([^']+)'/g)].map(([,id,key,keyword])=>({id,key,keyword}))
const keywords=Object.fromEntries(pages.map(({id,keyword})=>[id,keyword]))
assert.deepEqual(pages.map(page=>page.id),['general','models','permissions','shortcuts','voice','personality','agents','skills','tools','memory','connectors','plugins','mcpServer','hooks','automations'])
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {SettingsDialog} from '/src/components/overlays/SettingsDialog.tsx'
import {CommandPalette} from '/src/components/overlays/CommandPalette.tsx'
import {INSPECTOR_PANELS} from '/src/lib/inspectorPanels.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.panels=INSPECTOR_PANELS
const view=new URLSearchParams(location.search).get('view')
useAppStore.setState({settingsOpen:view==='settings',commandOpen:view==='commands',activeSessionId:null,sessions:[],workspace:null})
createRoot(document.getElementById('root')).render(React.createElement(view==='settings'?SettingsDialog:CommandPalette))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['settings','commands']){
  const page=await browser.newPage(),errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{requests.push(route.request().method());return route.fulfill({status:503,json:{code:'fixture/offline',error:'Original diagnostic'}})})
  await page.goto(server.resolvedUrls.local[0]+'__navigation-resource-chain?view='+view)
  let expectedQuery=''
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   if(view==='settings'){
    const search=page.getByRole('searchbox',{name:resources[locale]['ui.settings.search'],exact:true});assert.equal(await search.inputValue(),expectedQuery);await search.fill('')
    for(const group of ['agents','connections','advanced']){const button=page.getByRole('button',{name:resources[locale]['ui.settings.groups.'+group],exact:true});if(await button.getAttribute('aria-expanded')==='false')await button.click()}
    for(const {id,key} of pages){assert.ok(resources[locale][key]);await page.locator('nav button[data-page='+id+']').getByText(resources[locale][key],{exact:true}).waitFor()}
    for(const {id} of pages){
     assert.ok(resources[locale][keywords[id]])
     const token=resources[locale][keywords[id]].split(/\s+/).filter(Boolean)[0]
     await search.fill(token);await page.locator('nav button[data-page='+id+']').waitFor()
    }
    await search.fill('Original query no matches 原文');await page.getByText(resources[locale]['ui.settings.searchEmpty'],{exact:true}).waitFor()
   }else{
    const input=page.locator('[data-command-search]');assert.equal(await input.inputValue(),expectedQuery);await input.fill('')
    const panels=await page.evaluate(()=>window.panels)
    for(const panel of panels){const label=resources[locale][panel.labelKey];assert.ok(label);const title=resources[locale]['ui.components.overlays.CommandPalette.5dacde371a07'].replace('{p0}',label);await page.getByRole('option').getByText(title,{exact:true}).waitFor()}
    for(const {key} of pages)await page.getByRole('option').getByText(resources[locale][key],{exact:true}).waitFor()
    await input.fill('Original query no matches 原文')
   }
   expectedQuery='Original query no matches 原文'
  }
  assert.ok(requests.every(method=>method==='GET'));assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Settings navigation labels/keywords and command palette shared inspector resources switch en→zh→en; no settings actions executed')
},{entry:'/__navigation-resource-chain-entry.js',vite:{plugins:[{name:'navigation-resource-chain',resolveId(id){if(id==='/__navigation-resource-chain-entry.js')return id},load(id){if(id==='/__navigation-resource-chain-entry.js')return entry},configureServer(server){server.middlewares.use('/__navigation-resource-chain',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__navigation-resource-chain','<html><body><div id="root"></div><script type="module" src="/__navigation-resource-chain-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
