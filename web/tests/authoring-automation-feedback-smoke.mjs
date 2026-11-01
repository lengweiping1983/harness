import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AgentCreator} from '/src/components/overlays/AgentCreator.tsx'
import {AutomationsPage} from '/src/components/overlays/AutomationsPage.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',title:'User 原文',cwd:'/fixture'}]})
const params=new URLSearchParams(location.search),view=params.get('view'),kind=params.get('kind')||'skill'
createRoot(document.getElementById('root')).render(view==='author'?React.createElement(AgentCreator,{kind,sessionId:'fixture'}):React.createElement(AutomationsPage))
`
const payload={automations:[],runs:[],scheduler:{running:true,owner:true,last_error:null,max_concurrent:1}}
await browserScenario(async({server,browser})=>{
 for(const scenario of [{view:'author',kind:'skill',language:'en'},{view:'author',kind:'skill',language:'zh-CN'},{view:'author',kind:'plugin',language:'en'},{view:'author',kind:'plugin',language:'zh-CN'},...['load','preview','save'].map(view=>({view,kind:'skill',language:'en'}))]){
  const {view,kind,language}=scenario
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),path=new URL(request.url()).pathname
   requests.push({method:request.method(),path,body:request.postData(),id:request.headers()['idempotency-key'],requestId:request.headers()['x-request-id']})
   if(view==='author')return path.includes('/submissions/')?route.fulfill({json:{run:null}}):route.fulfill({status:200,body:'broken',contentType:'application/json'})
   if(view==='load'||view==='preview'&&path.endsWith('/preview')||view==='save'&&request.method()==='POST'&&!path.endsWith('/preview'))return route.fulfill({status:200,body:'broken',contentType:'application/json'})
   return route.fulfill({json:path.endsWith('/preview')?{occurrences:[1800000000]}:payload})
  })
  await page.goto(server.resolvedUrls.local[0]+'__authoring-automation-feedback?view='+view+'&kind='+kind)
  await page.evaluate(language=>window.locale(language),language)
  const key='ui.components.overlays.AutomationsPage.'
  if(view==='author'){
   const title=resources[language]['ui.authoring.title.'+kind]
   assert.ok(title);await page.getByRole('form',{name:title,exact:true}).waitFor()
   await page.getByRole('heading',{name:title,exact:true}).waitFor()
   await page.locator('textarea').fill('My requirements 原文')
   await page.locator('button[type=submit]').click()
  }else if(view==='preview'||view==='save'){
   await page.getByRole('button',{name:await page.evaluate(key=>window.copy(key+'6bee2372805a'),key),exact:true}).click()
   await page.clock.runFor(300)
   if(view==='save'){
    await page.getByRole('textbox',{name:await page.evaluate(key=>window.copy(key+'2479560deb33'),key),exact:true}).fill('Name 原文')
    await page.locator('textarea').fill('Prompt 原文')
    await page.locator('button[type=submit]').click()
   }
  }
  await page.getByRole('alert').waitFor()
  const before=requests.length
  const originalSubmission=view==='author'?requests.find(request=>request.method==='POST'):null
  if(originalSubmission){
   const task=resources[language]['ui.authoring.task.'+kind],requirements=resources[language]['ui.authoring.requirements']
   assert.ok(task&&requirements)
   assert.deepEqual(JSON.parse(originalSubmission.body),{text:'/skill '+kind+'-creator '+task+'\n\n'+requirements+'\nMy requirements 原文'})
   assert.ok(originalSubmission.id);assert.equal(originalSubmission.id,originalSubmission.requestId)
   assert.equal(requests.filter(request=>request.path.endsWith('/submissions/'+originalSubmission.id)).length,1)
  }
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   if(view==='author'){
    const title=resources[locale]['ui.authoring.title.'+kind]
    await page.getByRole('form',{name:title,exact:true}).waitFor();await page.getByRole('heading',{name:title,exact:true}).waitFor()
   }
   const expected=await page.evaluate(view=>window.copy(view==='author'?'ui.authoring.unknown':view==='save'?'ui.automations.creationUnconfirmed':'ui.http.invalidResponse'),view)
   await page.waitForFunction(expected=>Array.from(document.querySelectorAll('[role=alert]')).some(element=>element.textContent.includes(expected)),expected)
   assert.equal(requests.length,before,'locale must not repeat requests')
   if(view==='author'||view==='save')assert.equal(await page.locator('textarea').inputValue(),view==='author'?'My requirements 原文':'Prompt 原文')
  }
  if(view==='author'){
   assert.equal(requests.filter(request=>request.method==='POST').length,1)
   assert.equal(requests.filter(request=>request.path.includes('/submissions/')).length,1)
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.authoring.check')),exact:true}).click()
   await page.waitForFunction(()=>!document.querySelector('button[type=submit]')?.disabled)
   await page.getByRole('alert').waitFor()
   assert.equal(requests.filter(request=>request.path.endsWith('/submissions/'+originalSubmission.id)).length,2)
   assert.equal(requests.find(request=>request.method==='POST').body,originalSubmission.body)
   assert.equal(requests.filter(request=>request.method==='POST').length,1,'check must not replay original submission')
  }
  if(view==='save')assert.equal(requests.filter(request=>request.method==='POST'&&!request.path.endsWith('/preview')).length,1)
  assert.deepEqual(errors,[]);await page.close();console.log(view+': feedback follows locale, original input and operation identity retained')
 }
},{entry:'/__authoring-automation-feedback-entry.js',vite:{plugins:[{
 name:'authoring-automation-feedback-fixture',resolveId(id){if(id==='/__authoring-automation-feedback-entry.js')return id},load(id){if(id==='/__authoring-automation-feedback-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__authoring-automation-feedback',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__authoring-automation-feedback','<html><body><div id="root"></div><script type="module" src="/__authoring-automation-feedback-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
