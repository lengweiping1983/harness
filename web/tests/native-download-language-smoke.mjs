import assert from 'node:assert/strict'
import {BrowserDownloads} from '../../desktop/downloads.mjs'
import {NativeBrowserError} from '../../desktop/browser-errors.mjs'
import {browserScenario} from './browser-scenario.mjs'

const original='同名下载文件过多。请先检查下载目录后再操作。'
const downloads=[],cancellations=[]
for(const [filename,error] of [['Original-owned {name} 原文.bin',new NativeBrowserError('downloadNameLimit')],['Original-external {name} 原文.bin',Error(original)],['Original-combined {name} 原文.bin',new AggregateError([new NativeBrowserError('downloadNameLimit'),Error('External cleanup {name} 原文')],'browser/download-reservation-cleanup')]]){
 const producer=new BrowserDownloads(()=>{throw error})
 producer.add({getFilename:()=>filename,getInitiatorOrigin:()=> 'https://fixture.invalid',cancel:()=>cancellations.push(filename)},'owner')
 downloads.push(...producer.snapshot('owner'))
 assert.deepEqual(producer.snapshot('other'),[])
}
assert.equal(cancellations.length,3)
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {NativeBrowserPanel} from '/src/components/inspector/NativeBrowserPanel.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;setLocale('en');window.calls=[]
const state={session_id:'owner',active_tab:null,tabs:[],downloads:${JSON.stringify(downloads)}}
const subscribe=()=>()=>{}
window.harnessDesktop={onFocusFind:subscribe,onFocusAddress:subscribe,onBrowserState:subscribe,onBrowserError:subscribe,browser:async(action,args)=>{window.calls.push({action,args});return state}}
createRoot(document.getElementById('root')).render(React.createElement(NativeBrowserPanel,{sessionId:'owner'}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__native-download')
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const owned=page.getByRole('group',{name:downloads[0].filename,exact:true}),external=page.getByRole('group',{name:downloads[1].filename,exact:true})
  await owned.getByRole('alert').getByText(locale==='en'?'Too many files have this download name. Check the download folder before trying again.':original,{exact:true}).waitFor()
  await external.getByRole('alert').getByText(original,{exact:true}).waitFor()
  const combined=page.getByRole('group',{name:downloads[2].filename,exact:true})
  const primary=locale==='en'?'Too many files have this download name. Check the download folder before trying again.':original
  await combined.getByRole('alert').getByText(primary+(locale==='en'?' Cleanup also failed: ':' 清理也失败：')+'External cleanup {name} 原文',{exact:true}).waitFor()
  assert.equal(await owned.getByRole('button').count(),0)
  assert.equal(await external.getByRole('button').count(),0)
  assert.equal((await page.evaluate(()=>window.calls)).filter(call=>call.action==='download_action').length,0)
 }
 assert.deepEqual(errors,[])
 console.log('Native download producer -> panel: failed reservation keeps current-language resource, external original and filenames; en/zh/en themes/widths; no download actions')
},{entry:'/__native-download-entry.js',vite:{plugins:[{name:'native-download-language-fixture',resolveId(id){if(id==='/__native-download-entry.js')return id},load(id){if(id==='/__native-download-entry.js')return entry},configureServer(server){server.middlewares.use('/__native-download',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__native-download','<html><body><div id="root"></div><script type="module" src="/__native-download-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
