import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useRef} from 'react'
import {createRoot} from 'react-dom/client'
import {useAttachmentDrafts} from '/src/hooks/useAttachmentDrafts.ts'
import {ComposerAttachments} from '/src/components/chat/ComposerAttachments.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const view=new URLSearchParams(location.search).get('view')
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',cwd:'/fixture'}],toasts:[]})
if(view.startsWith('persist')&&!localStorage.getItem('harness.drafts.v1')){
 useDraftStore.getState().updateAttachments('fixture',()=>[{localId:'original',name:'User 原文.txt',size:4,mediaType:'text/plain',isImage:false,status:view==='persist-pending'?'uploading':'error',...(view==='persist-resource'?{error:{key:'ui.http.invalidResponse'}}:view==='persist-external'?{error:'Provider 原文'}:{})}])
 await useDraftStore.persist.rehydrate()
}
function Fixture(){const attachments=useAttachmentDrafts(),pickerRef=useRef(null);window.addFiles=attachments.addFiles;return React.createElement(React.Fragment,null,React.createElement(ComposerAttachments,{pickerRef,attachments}),React.createElement(Toaster))}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['upload-malformed','upload-empty','upload-external','persist-pending','persist-resource','persist-external','batch']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  let uploads=0;const errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/attachments',route=>{
   uploads++
   return view==='upload-malformed'?route.fulfill({status:200,body:'broken',contentType:'application/json'}):view==='upload-empty'?route.fulfill({json:{attachments:[]}}):route.fulfill({status:500,json:{code:'fixture/external',message:'Provider 原文'}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__attachment-feedback-language?view='+view)
  await page.waitForFunction(()=>typeof window.addFiles==='function')
  if(view.startsWith('upload'))await page.evaluate(()=>window.addFiles([new File(['text'],'User 原文.txt')]))
  if(view==='batch')await page.evaluate(()=>window.addFiles(['one.exe','two.sh','three.py','four.cmd'].map(name=>new File(['text'],name))))
  const expected=()=>page.evaluate(view=>{
   if(view.includes('external'))return 'Provider 原文'
   if(view==='persist-pending')return window.copy('ui.store.drafts.5dc58adf4cac')
   if(view==='upload-empty')return window.copy('ui.hooks.useAttachmentDrafts.2c89db12669c')
   if(view==='batch'){
    const items=['one.exe','two.sh','three.py'].map(name=>window.copy('ui.attachments.fileRejected',{name,error:window.copy('ui.lib.attachments.40adad246a3f',{p0:name.split('.').at(-1)})})).reduce((first,second)=>first?window.copy('ui.common.feedbackPair',{first,second}):second,'')
    return window.copy('ui.attachments.rejectionsTruncated',{items})
   }
   return window.copy('ui.http.invalidResponse')
  },view)
  const wait=async()=>page.waitForFunction(({expected,batch})=>batch?document.body.textContent.includes(expected):document.querySelector('[role=listitem]')?.title===expected,{expected:await expected(),batch:view==='batch'})
  await wait()
  if(view.startsWith('persist')){await page.reload();await wait()}
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await wait()
   assert.equal(uploads,view.startsWith('upload')?1:0,'locale and reload must not replay uploads')
   if(view!=='batch')assert.ok((await page.locator('[role=listitem]').innerText()).includes('User 原文.txt'))
  }
  assert.deepEqual(errors,[]);await page.close();console.log(view+': upload and persisted feedback follows locale, metadata and external diagnostics retained without replay')
 }
},{entry:'/__attachment-feedback-language-entry.js',vite:{plugins:[{
 name:'attachment-feedback-language-fixture',resolveId(id){if(id==='/__attachment-feedback-language-entry.js')return id},load(id){if(id==='/__attachment-feedback-language-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__attachment-feedback-language',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__attachment-feedback-language','<html><body><div id="root"></div><script type="module" src="/__attachment-feedback-language-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
