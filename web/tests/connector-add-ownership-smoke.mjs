import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ConnectorsPage} from '/src/components/overlays/EscPanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale(new URLSearchParams(location.search).get('locale')||'en');window.copy=t
createRoot(document.getElementById('root')).render(React.createElement(ConnectorsPage))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['replace','reopen','unchanged'])for(const [locale,dark,width] of [['en',false,390],['zh-CN',true,1440]]){
  const page=await browser.newPage({viewport:{width,height:900}}),errors=[],writes=[]
  page.on('pageerror',error=>errors.push(error.message))
  let release,entered
  const held=new Promise(resolve=>{entered=resolve})
  await page.route('**/api/v1/plugins/connectors/presets',route=>route.fulfill({json:{presets:[]}}))
  await page.route('**/api/v1/plugins/connectors',async route=>{
   if(route.request().method()==='POST'){
    writes.push(route.request().postDataJSON())
    if(writes.length===1){entered();await new Promise(resolve=>{release=resolve})}
    await route.fulfill({json:{ok:true}})
   }else await route.fulfill({json:{enabled:true,connectors:[],environment:[]}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__connector-add-ownership?locale='+locale)
  await page.evaluate(dark=>document.documentElement.classList.toggle('dark',dark),dark)
  const text=key=>page.evaluate(key=>window.copy('ui.components.overlays.EscPanel.'+key),key)
  const disclosure=page.getByRole('button',{name:await text('3f211352cd01'),exact:true})
  await disclosure.click()
  const name=page.getByRole('textbox',{name:await text('c925ddb9eb8a'),exact:true})
  const command=page.getByRole('textbox',{name:await text('8b76184a7d6e'),exact:true})
  await name.fill('original');await command.fill('original-command')
  const add=page.getByRole('button',{name:await text('7a8a11ead507'),exact:true})
  await add.click();await held
  if(mode==='replace'){
   await name.fill('replacement')
   await page.getByRole('combobox',{name:await text('84dada05d8d5'),exact:true}).selectOption('http')
   await page.getByRole('textbox',{name:await text('02739d1bae8b'),exact:true}).fill('https://replacement.test/mcp')
  }else if(mode==='reopen'){await disclosure.click();await disclosure.click();await name.waitFor()}
  const applied=page.waitForResponse(response=>response.url().endsWith('/plugins/connectors')&&response.request().method()==='POST')
  const checked=page.waitForResponse(response=>response.url().endsWith('/plugins/connectors')&&response.request().method()==='GET')
  release();await(await applied).finished();await checked
  assert.deepEqual(writes,[{name:'original',transport:'stdio',command:'original-command',args:[],enabled:true,bound:false}])
  if(mode==='unchanged')await name.waitFor({state:'hidden'})
  else{
   await add.waitFor();await page.waitForFunction(label=>!Array.from(document.querySelectorAll('button')).find(b=>b.textContent===label)?.disabled,await text('7a8a11ead507'))
   assert.equal(await name.isVisible(),true,'Late success must preserve replacement or reopened form')
   assert.equal(await name.inputValue(),mode==='replace'?'replacement':'original')
   if(mode==='replace')assert.equal(await page.getByRole('textbox',{name:await text('02739d1bae8b'),exact:true}).inputValue(),'https://replacement.test/mcp')
   await add.click();await name.waitFor({state:'hidden'});assert.equal(writes.length,2)
  }
  assert.deepEqual(errors,[]);await page.close();console.log('Connector add ownership: '+mode+' '+locale)
 }
},{entry:'/__connector-add-ownership-entry.js',vite:{plugins:[{name:'connector-add-ownership-fixture',resolveId(id){if(id==='/__connector-add-ownership-entry.js')return id},load(id){if(id==='/__connector-add-ownership-entry.js')return entry},configureServer(server){server.middlewares.use('/__connector-add-ownership',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__connector-add-ownership','<html><body><div id="root"></div><script type="module" src="/__connector-add-ownership-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
