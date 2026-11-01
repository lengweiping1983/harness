import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PlanHistory} from '/src/components/inspector/PlanHistory.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.reads=[];window.lists=[]
const version=index=>({id:'plan-'+index,title:'Original 计划 '+index,status:'pending',feedback:'',previous_id:null,seq:index,submitted_at:1,decided_at:null})
window.fetch=async(path,options={})=>{
 window.reads.push({path:String(path),method:options.method||'GET'})
 if(String(path).endsWith('/plans'))return new Promise(resolve=>window.lists.push(resolve))
 const index=Number(String(path).split('plan-').at(-1))
 return Response.json({plan:{...version(index),plan:'Original plan body '+index,diff:'',implementation:[]}})
}
window.answer=(count,failed=false)=>window.lists.shift()(failed?Response.json({message:'Controlled list failure'},{status:503}):Response.json({plans:Array.from({length:count},(_,index)=>version(index+1))}))
createRoot(document.getElementById('root')).render(React.createElement('main',{className:'min-h-screen bg-surface p-3 text-ink'},React.createElement(PlanHistory,{sessionId:'owner'})))
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage({viewport:{width:390,height:900}}),errors=[]
 page.setDefaultTimeout(5000)
 page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__plan-heading')
 await page.waitForFunction(()=>window.lists.length===1)
 await page.getByRole('heading',{name:'Plan',exact:true}).waitFor()
 const refresh=page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.inspector.PlanHistory.f7c3515f4e39')),exact:true})
 const matrix=async count=>{
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   const title=count?(locale==='en'?'Plan ('+count+(count===1?' version)':' versions)'):'计划（'+count+' 个版本）'):(locale==='en'?'Plan':'计划')
   const heading=page.getByRole('heading',{name:title,exact:true})
   await heading.waitFor()
   assert.equal(await page.locator('label > span').count(),0,'The count must not occupy another line above the version selector')
   assert.equal(await page.locator('body').evaluate(node=>node.scrollWidth<=innerWidth+1),true)
   if(count)await page.getByRole('combobox',{name:locale==='en'?'Plan version':'计划版本',exact:true}).waitFor()
   if(count===3&&locale==='zh-CN'&&!dark&&width===390&&process.env.HARNESS_TEST_PLAN_HEADING_SCREENSHOT)await page.locator('main').screenshot({path:process.env.HARNESS_TEST_PLAN_HEADING_SCREENSHOT})
  }
 }
 for(const count of [0,1,3]){
  if(count){await refresh.click();await page.waitForFunction(()=>window.lists.length===1)}
  await page.evaluate(count=>window.answer(count),count)
  await matrix(count)
 }
 const selector=page.getByRole('combobox',{name:'Plan version',exact:true})
 await selector.selectOption('plan-1')
 await page.getByText('Original plan body 1',{exact:true}).waitFor()
 const before=await page.evaluate(()=>window.reads.length)
 await matrix(3)
 assert.equal(await selector.inputValue(),'plan-1','Language changes must retain the selected version')
 assert.equal(await page.evaluate(()=>window.reads.length),before,'Presentation must not reload plan data')
 await refresh.click();await page.waitForFunction(()=>window.lists.length===1)
 await page.getByRole('heading',{name:'Plan (3 versions)',exact:true}).waitFor()
 await page.evaluate(()=>window.answer(0,true))
 await page.getByRole('alert').waitFor()
 await page.getByRole('heading',{name:'Plan (3 versions)',exact:true}).waitFor()
 assert.equal(await selector.inputValue(),'plan-1','A failed refresh retains the confirmed list and selection')
 assert.equal(await page.evaluate(()=>window.reads.some(read=>read.method!=='GET')),false)
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Plan title counts: no duplicate row, 0/1/multiple versions, selection preservation, failed refresh, en→zh→en and theme/width matrix passed')
},{entry:'/__plan-heading-entry.js',vite:{plugins:[{
 name:'plan-heading-fixture',resolveId(id){if(id==='/__plan-heading-entry.js')return id},load(id){if(id==='/__plan-heading-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__plan-heading',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__plan-heading','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__plan-heading-entry.js"></script></body></html>'))}catch(error){next(error)}})},
}]}})
