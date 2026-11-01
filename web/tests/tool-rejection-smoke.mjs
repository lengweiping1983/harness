import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ToolBlock} from '/src/components/chat/blocks/ToolBlock.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t
const codes=['ARGS','FS_EXISTS']
createRoot(document.getElementById('root')).render(React.createElement('main',null,...codes.map(code=>React.createElement('section',{'data-code':code,key:code},React.createElement(ToolBlock,{defaultOpen:true,block:{kind:'tool',at:0,id:code,callId:code,name:code==='ARGS'?'bash':'write',status:'error',input:{timeout:120000,path:"main.ts"},output:JSON.stringify({code,updated:false,message:'Historical diagnostic 原文'}),display:code==='ARGS'?{code:'tool/args',details:{not_executed:true}}:{code:'FS_EXISTS',details:{changed:false}}}})))))
`
await browserScenario(async({server,browser})=>{
  const page=await browser.newPage(), requests=[], errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
  await page.goto(server.resolvedUrls.local[0]+'__tool-rejection')
  for(const locale of ['en','zh-CN','en']) for(const dark of [false,true]) for(const width of [390,1440]) {
    await page.setViewportSize({width,height:900})
    await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
    for(const code of ['ARGS','FS_EXISTS']) {
      const section=page.locator('[data-code="'+code+'"]')
      const copy=await page.evaluate(code=>({reason:window.copy(code==='ARGS'?'ui.toolArgs.rejected':'ui.fileWrite.exists'),unchanged:window.copy(code==='ARGS'?'ui.toolArgs.bashTimeout':'ui.fileWrite.readFirst'),details:window.copy('ui.todoFailure.diagnostics')}),code)
      await section.locator('button[aria-controls]').getByText(copy.reason,{exact:true}).waitFor()
      await section.getByText(copy.unchanged,{exact:true}).waitFor()
      assert.equal(await section.getByText(/Historical diagnostic/).isVisible(),false)
      const summary=section.getByText(copy.details,{exact:true})
      await summary.focus();await page.keyboard.press('Enter')
      assert.equal(await section.getByText(/Historical diagnostic/).isVisible(),true)
      await page.keyboard.press('Enter')
      const fits=await section.evaluate(node=>node.scrollWidth<=node.clientWidth)
      assert.ok(fits,'Failure explanation must fit the viewport')
    }
  }
  assert.deepEqual(requests,[]);assert.deepEqual(errors,[])
  console.log('Tool rejection: current language, preserved diagnostics, keyboard disclosure, no writes; en→zh→en, themes and widths')
},{entry:'/__tool-rejection-entry.js',vite:{plugins:[{name:'tool-rejection-fixture',resolveId(id){if(id==='/__tool-rejection-entry.js')return id},load(id){if(id==='/__tool-rejection-entry.js')return entry},configureServer(server){server.middlewares.use('/__tool-rejection',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__tool-rejection','<html><body><div id="root"></div><script type="module" src="/__tool-rejection-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
