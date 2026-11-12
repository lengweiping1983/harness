import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ChatGPTLogin} from '/src/components/overlays/ChatGPTLogin.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.copy=t;window.locale=setLocale;window.modelWrites=0
window.models=()=>useAppStore.getState().models.map(model=>model.id)
useAppStore.setState({setDefaultModel:()=>{window.modelWrites++;return new Promise(resolve=>window.releaseSelection=()=>resolve(undefined))}})
createRoot(document.getElementById('root')).render(React.createElement(ChatGPTLogin,{onStatus:()=>{}}))
`
const model=id=>({id,label:id,provider:'chatgpt',description:'Original model',supports_reasoning:false,reasoning_efforts:[]})
await browserScenario(async({server,browser})=>{
 for(const mode of ['logout','selection']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
  let accounts=0,catalogs=0,writes=0,release,entered
  const held=new Promise(resolve=>entered=resolve),done=new Promise(resolve=>release=resolve)
  await page.route('**/api/v1/**',async route=>{
   const request=route.request(),url=new URL(request.url())
   if(url.pathname==='/api/v1/chatgpt'){
    accounts++
    const logged=mode==='selection'||accounts===2
    return route.fulfill({json:{logged_in:logged,email:logged?'Original@example.invalid':'',pending:false,error:'',models:mode==='selection'?[model('one'),model('two')]:[],selected:mode==='selection'?'one':''}})
   }
   if(url.pathname==='/api/v1/models'){
    catalogs++
    if(catalogs===1){entered();await done;return route.fulfill({json:{models:[model('old')],active:'old'}})}
    return route.fulfill({json:{models:[model('new')],active:'new'}})
   }
   if(url.pathname==='/api/v1/chatgpt/logout'){
    writes++;return route.fulfill({json:{logged_in:false,email:'',pending:false,error:'',models:[],selected:''}})
   }
   throw Error('Unexpected account request '+url)
  })
  await page.goto(server.resolvedUrls.local[0]+'__chatgpt-catalog-ownership')
  await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.chatgpt.login')),exact:true}).waitFor()
  if(mode==='logout'){
   await page.evaluate(()=>window.dispatchEvent(new Event('chatgpt-account-changed')))
   await held
   const logout=page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.chatgpt.logout')),exact:true})
   await logout.evaluate(element=>{const props=element[Object.keys(element).find(key=>key.startsWith('__reactProps$'))];props.onClick();props.onClick()})
   await page.waitForFunction(()=>window.models().includes('new'))
   release();await page.waitForTimeout(30)
   assert.deepEqual(await page.evaluate(()=>window.models()),['new'])
   assert.equal(writes,1);assert.equal(catalogs,2)
  }else{
   await page.getByRole('combobox').evaluate(element=>{
    const props=element[Object.keys(element).find(key=>key.startsWith('__reactProps$'))]
    window.selection=props.onChange({target:{value:'two'}});props.onChange({target:{value:'two'}})
    const logout=Array.from(document.querySelectorAll('button')).find(button=>button.textContent.includes(window.copy('ui.chatgpt.logout')))
    logout[Object.keys(logout).find(key=>key.startsWith('__reactProps$'))].onClick()
   })
   await page.waitForFunction(()=>window.modelWrites===1)
   assert.equal(writes,0)
   await page.evaluate(()=>window.releaseSelection())
   await page.evaluate(()=>window.selection)
   assert.equal(await page.evaluate(()=>window.modelWrites),1)
  }
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
   assert.equal(writes,mode==='logout'?1:0)
  }
  assert.deepEqual(errors,[]);await page.close()
  console.log('ChatGPT model catalog operation ownership: '+mode)
 }
},{entry:'/__chatgpt-catalog-ownership-entry.js',vite:{plugins:[{
 name:'chatgpt-catalog-ownership-fixture',resolveId(id){if(id==='/__chatgpt-catalog-ownership-entry.js')return id},load(id){if(id==='/__chatgpt-catalog-ownership-entry.js')return entry},configureServer(server){server.middlewares.use('/__chatgpt-catalog-ownership',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__chatgpt-catalog-ownership','<html><body><div id="root"></div><script type="module" src="/__chatgpt-catalog-ownership-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
