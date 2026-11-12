import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {RenameDialog} from '/src/components/overlays/RenameDialog.tsx'
import {useAppStore} from '/src/store/app.ts'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;window.requests=[];window.closes=[];window.app=useAppStore
setLocale('en');useAppStore.setState({sessions:[{id:'a',title:'Original A'},{id:'b',title:'Original B'}],nameSaves:{}})
api.updateSession=(id,body)=>new Promise(resolve=>window.requests.push({id,body,resolve}))
window.finish=(index,success)=>{const request=window.requests[index];request.resolve(success?{ok:true,data:{session:{id:request.id,title:request.body.title}}}:{ok:false,status:403,code:'access/denied',message:'Old snapshot',copy:{key:'ui.http.invalidResponse'}})}
function Fixture(){const [target,setTarget]=useState({kind:'session',id:'a',value:'Original A'});window.replace=()=>setTarget({kind:'session',id:'b',value:'Original B'});return target?React.createElement(RenameDialog,{key:target.id,target,onClose:()=>{window.closes.push(target.id);setTarget(null)}}):null}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const success of [true,false]){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0]+'__rename-ownership')
  await page.getByRole('textbox').fill('New name 原文 {name}')
  await page.locator('form').evaluate(form=>{form.requestSubmit();form.requestSubmit()})
  await page.waitForFunction(()=>window.requests.length===1)
  await page.evaluate(()=>window.replace())
  await page.waitForFunction(()=>document.querySelector('input')?.value==='Original B')
  await page.evaluate(success=>window.finish(0,success),success)
  await page.waitForFunction(()=>Object.keys(window.app.getState().nameSaves).length===0)
  assert.equal(await page.getByRole('dialog').count(),1)
  assert.equal(await page.getByRole('textbox').inputValue(),'Original B')
  assert.deepEqual(await page.evaluate(()=>window.closes),[])
  assert.equal(await page.getByRole('alert').count(),0)
  assert.equal(await page.evaluate(()=>window.app.getState().sessions.find(row=>row.id==='a').title),success?'New name 原文 {name}':'Original A')
  await page.getByRole('textbox').fill('Current B 原文')
  await page.locator('form').evaluate(form=>form.requestSubmit())
  await page.waitForFunction(()=>window.requests.length===2)
  await page.evaluate(()=>window.finish(1,false))
  for(const locale of ['en','zh-CN','en']){
   await page.evaluate(locale=>window.locale(locale),locale)
   const expected=await page.evaluate(()=>window.copy('ui.http.invalidResponse'))
   await page.getByRole('alert').filter({hasText:expected}).waitFor()
   assert.equal(await page.getByRole('textbox').inputValue(),'Current B 原文')
   assert.equal(await page.evaluate(()=>window.requests.length),2)
  }
  await page.locator('form').evaluate(form=>form.requestSubmit())
  await page.waitForFunction(()=>window.requests.length===3)
  await page.evaluate(()=>window.finish(2,true))
  await page.getByRole('dialog').waitFor({state:'detached'})
  assert.deepEqual(await page.evaluate(()=>window.closes),['b'])
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Rename ownership: old writes settle without closing/revising replacement; current refusal preserves localized feedback and retry closes once')
},{entry:'/__rename-ownership-entry.js',vite:{plugins:[{name:'rename-ownership',resolveId(id){if(id==='/__rename-ownership-entry.js')return id},load(id){if(id==='/__rename-ownership-entry.js')return entry},configureServer(server){server.middlewares.use('/__rename-ownership',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__rename-ownership','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__rename-ownership-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
