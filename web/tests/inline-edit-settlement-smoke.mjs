import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {MessageRow} from '/src/components/chat/MessageList.tsx'
import {MessageEditContext} from '/src/components/chat/MessageEditContext.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {useChatStore,emptySession} from '/src/store/chat.ts'
import {useAppStore} from '/src/store/app.ts'
import {ResourceError} from '/src/lib/resourceError.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[]
const params=new URLSearchParams(location.search),running=params.get('running')==='1'
useAppStore.setState({activeSessionId:'owner'})
useChatStore.setState({sessions:{owner:{...emptySession(),running},other:emptySession()}})
useDraftStore.getState().setInlineEdit('owner','7','Submitted edit 原文')
window.replace=()=>useDraftStore.getState().setInlineEdit('owner','7','Newer edit 原文')
window.snapshot=()=>useDraftStore.getState().bySession
function call(kind,text){window.calls.push({kind,text});return new Promise((resolve,reject)=>{window.finish=outcome=>outcome==='error'?reject(new ResourceError({key:'ui.messageEdit.failed'})):resolve(outcome==='accepted')})}
const channel={connection:params.get('offline')==='1'?'closed':'open',send:text=>call('send',text),steer:text=>call('steer',text)}
const block={kind:'user',id:'shared-id',sourceSeq:7,at:0,text:'Original message 原文'}
function App(){const [session,setSession]=useState('owner');window.switch=()=>{useAppStore.setState({activeSessionId:'other'});useDraftStore.getState().setInlineEdit('other','7','Other draft 原文');setSession('other')};return React.createElement(MessageEditContext.Provider,{value:{sessionId:session,channel}},React.createElement(MessageRow,{block,context:{sessionId:session,onAnswer:()=>{}}}))}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['accepted-current','accepted-new','error-current','error-new','rejected-current','switch','switch-error','offline'])for(const running of [false,true]){
  const page=await browser.newPage(),errors=[],requests=[];page.setDefaultTimeout(15000)
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
  await page.goto(server.resolvedUrls.local[0]+'__inline-settlement?running='+(running?'1':'0')+'&offline='+(mode==='offline'?'1':'0'))
  const editor=page.getByRole('textbox');await editor.waitFor()
  for(const key of ['Enter','Escape'])for(const composition of [{isComposing:true},{keyCode:229}]){
   await editor.evaluate((node,{key,composition})=>node.dispatchEvent(new KeyboardEvent('keydown',{key,ctrlKey:true,bubbles:true,cancelable:true,...composition})),{key,composition})
   assert.equal(await page.evaluate(()=>window.calls.length),0);assert.equal(await editor.inputValue(),'Submitted edit 原文')
  }
  await editor.press('Control+Enter')
  if(mode==='offline')await page.getByRole('alert').waitFor()
  else{
   await page.waitForFunction(()=>window.calls.length===1)
   if(mode.endsWith('new'))await page.evaluate(()=>window.replace())
   if(mode.startsWith('switch'))await page.evaluate(()=>window.switch())
   await page.evaluate(outcome=>window.finish(outcome),mode.includes('error')?'error':mode.startsWith('rejected')?'rejected':'accepted')
   if(mode==='accepted-current')await editor.waitFor({state:'detached'})
   else await page.waitForFunction(()=>!document.querySelector('textarea')?.disabled)
  }
  const expectedError=mode==='error-current'?'ui.messageEdit.failed':mode==='rejected-current'||mode==='offline'?'ui.hooks.useSessionSocket.e1e257086621':null
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   if(expectedError)await page.getByRole('alert').getByText(await page.evaluate(key=>window.copy(key),expectedError),{exact:true}).waitFor()
   else assert.equal(await page.getByRole('alert').count(),0)
   if(mode!=='accepted-current')assert.equal(await editor.inputValue(),mode.startsWith('switch')?'Other draft 原文':mode.endsWith('new')?'Newer edit 原文':'Submitted edit 原文')
   assert.equal(await page.evaluate(()=>window.calls.length),mode==='offline'?0:1)
  }
  if(mode!=='offline')assert.deepEqual(await page.evaluate(()=>window.calls),[{kind:running?'steer':'send',text:'Submitted edit 原文'}])
  assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);await page.close()
  console.log(mode+' '+(running?'steer':'send')+': scoped settlement, live error resources and IME protection')
 }
},{entry:'/__inline-settlement-entry.js',vite:{plugins:[{name:'inline-settlement-fixture',resolveId(id){if(id==='/__inline-settlement-entry.js')return id},load(id){if(id==='/__inline-settlement-entry.js')return entry},configureServer(server){server.middlewares.use('/__inline-settlement',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__inline-settlement','<html><body><div id="root"></div><script type="module" src="/__inline-settlement-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
