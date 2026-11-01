import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AutomationsPage} from '/src/components/overlays/AutomationsPage.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;window.reads=0;window.posts=0;setLocale('en')
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',title:'Original conversation 原文',cwd:'/fixture'}]})
const mode=new URLSearchParams(location.search).get('mode')
const catalog=name=>({automations:[{id:'task',name,prompt:'Original prompt 原文',session_id:'fixture',mode:'session',schedule:{kind:'interval',seconds:60},timezone:'UTC',due:1800000000,enabled:1,running:0,failures:0,last_error:null}],runs:[],scheduler:{running:true,owner:true,last_error:null,max_concurrent:1}})
const response=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}})
window.fetch=async(path,options={})=>{
 if(path.endsWith('/preview'))return response({occurrences:[1800000000]})
 if((options.method||'GET')==='GET'){
  window.reads++
  if(window.reads===2){window.oldSignal=options.signal;return new Promise(resolve=>{window.releaseOld=()=>resolve(response(catalog('Obsolete task')))})}
  return response(catalog(window.reads===1?'Original task':'Current task'))
 }
 window.posts++
 if(mode==='save'){const body=JSON.parse(options.body);return new Promise(resolve=>{window.finishWrite=()=>resolve(response({...body,id:'auto_'+body.creation_id,enabled:Number(body.enabled)}))})}
 return response({})
}
createRoot(document.getElementById('root')).render(React.createElement(AutomationsPage))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['refresh','run','save']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  await page.goto(server.resolvedUrls.local[0]+'__automation-owner?mode='+mode)
  await page.getByText('Original task',{exact:true}).waitFor()
  await page.clock.runFor(4000);await page.waitForFunction(()=>typeof window.releaseOld==='function')
  const label=suffix=>page.evaluate(suffix=>window.copy('ui.components.overlays.AutomationsPage.'+suffix),suffix)
  if(mode==='save'){
   await page.getByRole('button',{name:await label('6bee2372805a'),exact:true}).click()
   await page.clock.runFor(300)
   await page.getByRole('textbox',{name:await label('2479560deb33'),exact:true}).fill('New task 原文')
   await page.locator('textarea').fill('New prompt 原文')
   await page.locator('button[type=submit]').waitFor({state:'visible'})
   await page.waitForFunction(()=>!document.querySelector('button[type=submit]').disabled)
   await page.locator('form').evaluate(form=>{
    const input=form.querySelector('input')
    input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:'原'}))
    form.requestSubmit()
   })
   assert.equal(await page.evaluate(()=>window.posts),0,'composition must not submit an automation')
   for(const options of [{isComposing:true},{keyCode:229}]){
    assert.equal(await page.locator('form input').first().evaluate((input,options)=>{
     const event=new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true,...options})
     input.dispatchEvent(event);return event.defaultPrevented
    },options),true,'IME Enter must not trigger implicit submission')
   }
   await page.locator('form').evaluate(form=>{
    form.querySelector('input').dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'原文'}))
    form.requestSubmit();form.requestSubmit()
   })
   await page.waitForFunction(()=>window.posts===1);assert.equal(await page.evaluate(()=>window.posts),1)
   await page.evaluate(()=>window.finishWrite())
  }else{
   const button=page.getByRole('button',{name:await label(mode==='run'?'04eb539709cf':'4458d5c5befb'),exact:true})
   await button.evaluate(element=>{element.click();element.click()})
  }
  await page.getByText('Current task',{exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>window.oldSignal.aborted),true)
  await page.evaluate(()=>window.releaseOld())
  await page.clock.runFor(32)
  assert.equal(await page.evaluate(()=>window.reads),3,'one operation readback, no duplicate refresh')
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await page.getByText('Current task',{exact:true}).waitFor();assert.equal(await page.getByText('Obsolete task',{exact:true}).count(),0)
   assert.equal(await page.evaluate(()=>window.posts),mode==='refresh'?0:1);assert.equal(await page.evaluate(()=>window.reads),3)
  }
  await page.clock.runFor(4000);await page.waitForFunction(()=>window.reads===4)
  assert.equal(await page.evaluate(()=>window.posts),mode==='refresh'?0:1)
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.testHidden});window.testHidden=true;document.dispatchEvent(new Event('visibilitychange'))})
  await page.clock.runFor(8000);assert.equal(await page.evaluate(()=>window.reads),4)
  await page.evaluate(()=>{window.testHidden=false;document.dispatchEvent(new Event('visibilitychange'))})
  await page.waitForFunction(()=>window.reads===5);await page.clock.runFor(4032)
  assert.equal(await page.evaluate(()=>window.reads),6,'one observer resumes, with no duplicate timer')
  assert.deepEqual(errors,[]);await page.close();console.log('Automation admission, latest catalog, single refresh and resumed polling: '+mode)
 }
},{entry:'/__automation-owner-entry.js',vite:{plugins:[{name:'automation-owner-fixture',resolveId(id){if(id==='/__automation-owner-entry.js')return id},load(id){if(id==='/__automation-owner-entry.js')return entry},configureServer(server){server.middlewares.use('/__automation-owner',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__automation-owner','<html><body><div id="root"></div><script type="module" src="/__automation-owner-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
