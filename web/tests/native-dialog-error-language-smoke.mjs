import assert from 'node:assert/strict'
import {NativeDialogs} from '../../desktop/dialogs.mjs'
import {readFileSync} from 'node:fs'
import {browserFailures} from '../../desktop/tests/browser-failures.mjs'
import {browserScenario} from './browser-scenario.mjs'

const failures=[]
for(const kind of ['inactive','pending','invalid']){
 const dialogs=new NativeDialogs()
 dialogs.open('tab','confirm','Original website {name} 原文','https://fixture.invalid','',()=>assert.fail('rejected answers must not execute'))
 const entry=dialogs.pending.get('tab')
 if(kind==='pending')entry.response_unconfirmed=true
 try{await dialogs.respond('tab',{isDestroyed:()=>false},{id:kind==='inactive'?'stale':entry.id,accept:kind==='invalid'?'yes':true});assert.fail('expected rejection')}
 catch(error){assert.equal(error.code,'browser/dialog-'+kind);failures.push({failure:{code:error.code,copy:error.copy}})}
}
for(const {code,copy} of browserFailures().slice(3))failures.push({failure:{code,copy}})
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {NativeBrowserPanel} from '/src/components/inspector/NativeBrowserPanel.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
const failures=${JSON.stringify(failures)}
window.locale=setLocale;setLocale('en');window.calls=[];let mode=0
const subscribe=()=>()=>{}
window.harnessDesktop={onFocusFind:subscribe,onFocusAddress:subscribe,onBrowserState:subscribe,onBrowserError:subscribe,browser:async(action,args)=>{
 window.calls.push({action,args})
 if(action==='dialog_response'){if(mode===failures.length)throw Error('External original {name} 原文');return failures[mode]}
 return {session_id:'owner',active_tab:'tab',tabs:[{id:'tab',url:'https://fixture.invalid',title:'Original',loading:false,can_go_back:false,can_go_forward:false}],dialog:{id:'Original-'+mode,type:'confirm',message:'Original website {name} 原文',url:'https://fixture.invalid'}}
}}
const root=createRoot(document.getElementById('root'))
window.show=index=>{mode=index;root.render(React.createElement(NativeBrowserPanel,{key:index,sessionId:'owner'}))}
window.show(0)
`
const texts=Object.fromEntries(['en','zh-CN'].map(locale=>{
 const resources=JSON.parse(readFileSync(new URL('../../harness/i18n/locales/'+locale+'.json',import.meta.url)))
 return [locale,failures.map(({failure})=>resources[failure.copy.key].replace(/\{([^}]+)\}/g,(_match,key)=>String(failure.copy.params[key])))]
}))
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__native-dialog-error')
 for(let index=0;index<=failures.length;index++){
  await page.evaluate(index=>{window.locale('en');window.show(index)},index)
  await page.getByText('Original website {name} 原文',{exact:true}).waitFor()
  await page.getByRole('button',{name:'Confirmation dialog',exact:true}).click()
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   await page.getByRole('alert').getByText(index===failures.length?'External original {name} 原文':texts[locale][index],{exact:true}).waitFor()
   const writes=(await page.evaluate(()=>window.calls)).filter(call=>call.action==='dialog_response')
   assert.equal(writes.length,index+1)
   assert.deepEqual(writes[index].args,{session_id:'owner',id:'Original-'+index,accept:true,prompt_text:undefined})
  }
 }
 assert.deepEqual(errors,[])
 console.log('Native browser errors: 34 resources and unchanged external diagnostic, en/zh/en themes/widths, no response replay')
},{entry:'/__native-dialog-error-entry.js',vite:{plugins:[{name:'native-dialog-error-language-fixture',resolveId(id){if(id==='/__native-dialog-error-entry.js')return id},load(id){if(id==='/__native-dialog-error-entry.js')return entry},configureServer(server){server.middlewares.use('/__native-dialog-error',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__native-dialog-error','<html><body><div id="root"></div><script type="module" src="/__native-dialog-error-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
