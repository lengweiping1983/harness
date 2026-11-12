import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {PersonalityPanel} from '/src/components/overlays/PersonalityPanel.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const root=createRoot(document.getElementById('root'));root.render(React.createElement(PersonalityPanel));window.unmount=()=>root.unmount()
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['save','saveApply','select','suggest','delete','saveUnknown','saveApplyUnknown','saveUnmount']){
  const page=await browser.newPage(),errors=[],unexpected=[],requests=[];page.setDefaultTimeout(15000)
  page.on('pageerror',error=>errors.push(error.message))
  const built={id:'builtin:delivery-lead',source:'builtin',code:'delivery-lead',name:'Builtin original 原文',description:'Original summary 原文',prompt:'Original prompt 原文'}
  const user={...built,id:'a'.repeat(32),source:'user',code:null,name:'User original 原文',revision:'b'.repeat(64)}
  let profiles=[built,user],selection={profile:null,revision:'c'.repeat(64)},release,enter,heldOnce=false
  const gate=new Promise(resolve=>{release=resolve}),held=new Promise(resolve=>{enter=resolve})
  await page.route('**/api/v1/**',async route=>{
   const request=route.request(),path=new URL(request.url()).pathname,method=request.method(),data=request.postDataJSON();requests.push({path,method,data})
   if(method!=='GET'&&!heldOnce){heldOnce=true;enter();await gate}
   if(path==='/api/v1/personalities'&&method==='GET')return route.fulfill({json:{profiles}})
   if(path==='/api/v1/personality-selection'&&method==='GET')return route.fulfill({json:selection})
   if(path==='/api/v1/personalities'&&method==='POST'){const saved={...user,...data,id:'d'.repeat(32),code:null,revision:'e'.repeat(64)};profiles=[...profiles,saved];if(mode==='saveUnknown')return route.fulfill({status:503,json:{error:'Original unconfirmed 原文'}});return route.fulfill({json:saved})}
   if(path==='/api/v1/personality-selection'&&method==='PATCH'){selection={profile:profiles.find(p=>p.id===data.profile_id)??null,revision:'f'.repeat(64)};if(mode==='saveApplyUnknown')return route.fulfill({status:503,json:{error:'Original unconfirmed 原文'}});return route.fulfill({json:selection})}
   if(path==='/api/v1/personalities/recommend'&&method==='POST')return route.fulfill({json:{code:'delivery-lead'}})
   if(path==='/api/v1/personalities/'+user.id&&method==='DELETE'){profiles=profiles.filter(p=>p.id!==user.id);return route.fulfill({json:{deleted:user.id}})}
   unexpected.push({path,method});return route.fulfill({status:500,json:{error:'Unexpected request'}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__personality-operation')
  const copy=key=>page.evaluate(key=>window.copy(key),key)
  const button=async key=>page.getByRole('button',{name:await copy(key),exact:true})
  await page.getByText(built.name,{exact:true}).waitFor()
  let action
  if(mode.startsWith('save')){
   await (await button('ui.personality.create')).click()
   await page.getByRole('textbox',{name:await copy('ui.personality.name'),exact:true}).fill('New name 原文')
   await page.getByRole('textbox',{name:await copy('ui.personality.content'),exact:true}).fill('New prompt 原文')
   action=await button(mode==='saveApply'||mode==='saveApplyUnknown'||mode==='saveUnmount'?'ui.personality.saveAndUse':'ui.personality.save')
  }else if(mode==='select')action=page.locator('article').filter({hasText:built.name}).getByRole('button',{name:await copy('ui.personality.apply'),exact:true})
  else if(mode==='suggest'){
   await page.locator('summary').getByText(await copy('ui.personality.quiz'),{exact:true}).click();for(const value of ['proactive','conceptual','analytical','structured'])await page.locator('input[value="'+value+'"]').check();action=await button('ui.personality.recommend')
  }else{
   await page.locator('article').filter({hasText:user.name}).getByRole('button',{name:await copy('ui.personality.viewDetails'),exact:true}).click()
   await (await button('ui.personality.edit')).click()
   await page.locator('summary').getByText(await copy('ui.personality.moreActions'),{exact:true}).click()
   await (await button('ui.personality.delete')).click();action=await button('ui.personality.confirmDelete')
  }
  await action.evaluate(node=>{node.click();node.click()});await Promise.race([held,new Promise((_,reject)=>{const timer=setTimeout(()=>reject(Error('Personality write was not accepted within 15 seconds')),15000);timer.unref();held.finally(()=>clearTimeout(timer))})])
  const reads=requests.filter(row=>row.method==='GET').length
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   assert.equal(requests.filter(row=>row.method!=='GET').length,1)
   assert.equal(requests.filter(row=>row.method==='GET').length,reads)
   if(mode.startsWith('save')){
    assert.equal(await page.getByRole('textbox',{name:await copy('ui.personality.content'),exact:true}).inputValue(),'New prompt 原文')
    assert.equal(await (await button(mode==='saveApply'||mode==='saveApplyUnknown'||mode==='saveUnmount'?'ui.personality.saveAndUse':'ui.personality.save')).isDisabled(),true)
   }
   if(mode==='delete')assert.equal(await (await button('ui.personality.confirmDelete')).isDisabled(),true)
   if(mode==='suggest')assert.equal(await page.locator('input[value="proactive"]').isChecked(),true)
  }
  if(mode==='saveUnmount'){
   await page.evaluate(()=>window.unmount());release()
   await page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/personalities'))
   assert.equal(requests.filter(row=>row.method!=='GET').length,1)
   assert.equal(requests.filter(row=>row.method==='PATCH').length,0)
   assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);await page.close();console.log('saveUnmount: accepted save completes without applying after unmount');continue
  }
  release()
  const label=await copy('ui.personality.refresh')
  await page.waitForFunction(label=>[...document.querySelectorAll('button')].some(node=>node.textContent===label&&!node.disabled),label)
  assert.equal(requests.filter(row=>row.method!=='GET').length,mode==='saveApply'||mode==='saveApplyUnknown'?2:1)
  if(mode==='saveApplyUnknown'){
   await page.getByRole('alert').filter({hasText:'Original unconfirmed 原文'}).waitFor()
   assert.equal(requests.filter(row=>row.method==='GET').length,reads+2)
   assert.equal(requests.filter(row=>row.method==='POST').length,1)
   assert.equal(requests.filter(row=>row.method==='PATCH').length,1)
   assert.equal(await page.getByRole('dialog').count(),0)
   assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);await page.close();console.log('saveApplyUnknown: saved identity retained, selection read back, no replay');continue
  }
  if(mode==='saveUnknown'){
   await page.getByRole('alert').filter({hasText:'Original unconfirmed 原文'}).waitFor()
   assert.equal(requests.filter(row=>row.method==='GET').length,reads+2)
   assert.equal(await page.getByRole('textbox',{name:await copy('ui.personality.content'),exact:true}).inputValue(),'New prompt 原文')
   assert.equal(await page.getByRole('dialog').count(),1)
   for(const locale of ['zh-CN','en']){await page.evaluate(locale=>window.locale(locale),locale);await page.getByRole('alert').filter({hasText:'Original unconfirmed 原文'}).waitFor()}
   assert.equal(requests.filter(row=>row.method!=='GET').length,1)
   assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);await page.close();console.log('saveUnknown: readback without write replay and original draft retained');continue
  }
  if(mode==='suggest')assert.deepEqual(requests.find(row=>row.path.endsWith('/recommend')).data.choices,['proactive','conceptual','analytical','structured'])
  else await page.getByText(await copy(mode==='save'?'ui.personality.librarySaved':mode==='delete'?'ui.personality.deleted':'ui.personality.globalSaved'),{exact:true}).waitFor()
  if(mode.startsWith('save'))assert.equal(await page.getByRole('dialog').count(),0)
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);await page.close();console.log(mode+': language preserves write ownership, editor and preference choices; one accepted operation')
 }
},{entry:'/__personality-operation-entry.js',vite:{plugins:[{name:'personality-operation-fixture',resolveId(id){if(id==='/__personality-operation-entry.js')return id},load(id){if(id==='/__personality-operation-entry.js')return entry},configureServer(server){server.middlewares.use('/__personality-operation',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__personality-operation','<html><body><div id="root"></div><script type="module" src="/__personality-operation-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
