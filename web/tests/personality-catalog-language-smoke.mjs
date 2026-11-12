import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PersonalityPanel} from '/src/components/overlays/PersonalityPanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
createRoot(document.getElementById('root')).render(React.createElement(PersonalityPanel))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],writes=[]
 page.on('pageerror',error=>errors.push(error.message))
 let language='en'
 const profile=()=>({id:'delivery-lead',code:'delivery-lead',source:'builtin',
  name:language==='en'?'Delivery plan':'执行计划',description:language==='en'?'Built-in English summary':'内置中文说明',
  prompt:language==='en'?'Built-in English prompt {name}':'内置中文提示词 {name}'})
 await page.route('**/api/v1/**',async route=>{
  const method=route.request().method(),path=new URL(route.request().url()).pathname
  if(method!=='GET')writes.push({method,path})
  if(method==='GET'&&path==='/api/v1/personalities')return route.fulfill({json:{profiles:[profile()]}})
  if(method==='GET'&&path==='/api/v1/personality-selection')return route.fulfill({json:{profile:null,revision:'a'.repeat(64)}})
  if(method==='POST'&&path==='/api/v1/personalities/recommend')return route.fulfill({json:{code:'delivery-lead'}})
  return route.fulfill({status:500,json:{error:'Unexpected request'}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__personality-catalog')
 const copy=key=>page.evaluate(key=>window.copy(key),key)
 const button=async key=>page.getByRole('button',{name:await copy(key),exact:true})
 await page.locator('article').filter({hasText:profile().name}).getByRole('button',{name:await copy('ui.personality.viewDetails'),exact:true}).click()
 for(const locale of ['en','zh-CN','en','zh-CN']){
  language=locale;await page.evaluate(locale=>window.locale(locale),locale)
  await page.locator('article').filter({hasText:profile().name}).waitFor()
  const field=page.getByRole('textbox',{name:await copy('ui.personality.content'),exact:true})
  assert.equal(await field.inputValue(),profile().prompt)
  assert.equal(await field.getAttribute('readonly'),'')
  assert.equal(await page.getByRole('dialog').getByRole('heading').textContent(),profile().name)
 }
 assert.equal(writes.length,0)
 await (await button('ui.personality.customize')).click()
 let field=page.getByRole('textbox',{name:await copy('ui.personality.content'),exact:true})
 assert.equal(await field.inputValue(),profile().prompt)
 await field.fill('User prompt 原文 {name}')
 await page.getByRole('textbox',{name:await copy('ui.personality.name'),exact:true}).fill('User name 原文 {name}')
 await (await button('ui.personality.closeEditor')).click()
 language='en';await page.evaluate(()=>window.locale('en'))
 await page.locator('article').filter({hasText:profile().name}).getByRole('button',{name:await copy('ui.personality.viewDetails'),exact:true}).click()
 await (await button('ui.personality.customize')).click()
 assert.equal(await page.getByRole('textbox',{name:await copy('ui.personality.content'),exact:true}).inputValue(),'User prompt 原文 {name}')
 assert.equal(await page.getByRole('textbox',{name:await copy('ui.personality.name'),exact:true}).inputValue(),'User name 原文 {name}')
 await (await button('ui.personality.closeEditor')).click()
 await page.locator('summary').getByText(await copy('ui.personality.quiz'),{exact:true}).click()
 assert.equal(await page.locator('input[type=radio]:checked').count(),0)
 const axes=[['reflective','proactive'],['conceptual','concrete'],['analytical','empathetic'],['structured','adaptive']]
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  language=locale;await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(let index=0;index<axes.length;index++){
   const axisKey='ui.personality.axis'+index;assert.ok(resources[locale][axisKey]);await page.locator('legend').getByText(resources[locale][axisKey],{exact:true}).waitFor()
   for(const value of axes[index]){const key='ui.personality.'+value;assert.ok(resources[locale][key]);const radio=page.getByRole('radio',{name:resources[locale][key],exact:true});await radio.check();assert.equal(await radio.isChecked(),true)}
  }
  for(const source of ['user','builtin','all']){
   const key='ui.personality.tab.'+source;assert.ok(resources[locale][key]);const tab=page.getByRole('tab',{name:resources[locale][key],exact:true});await tab.click();assert.equal(await tab.getAttribute('aria-selected'),'true')
  }
  assert.equal(await page.locator('input[type=radio]:checked').count(),4);assert.equal(writes.length,0)
 }
 for(const value of ['proactive','conceptual','analytical','structured'])await page.locator('input[value="'+value+'"]').check()
 await (await button('ui.personality.recommend')).click()
 for(const locale of ['en','zh-CN','en']){
  language=locale;await page.evaluate(locale=>window.locale(locale),locale)
  await page.getByRole('status').filter({hasText:profile().name}).waitFor()
 }
 assert.deepEqual(writes,[{method:'POST',path:'/api/v1/personalities/recommend'}])
 assert.deepEqual(errors,[]);await page.close()
 console.log('Personality catalog: built-in details and copied defaults follow locale; user drafts remain original; recommendation uses current prose without replay')
},{entry:'/__personality-catalog-entry.js',vite:{plugins:[{name:'personality-catalog',resolveId(id){if(id==='/__personality-catalog-entry.js')return id},load(id){if(id==='/__personality-catalog-entry.js')return entry},configureServer(server){server.middlewares.use('/__personality-catalog',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__personality-catalog','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__personality-catalog-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
