import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueueControlDock} from '/src/components/chat/QueueControlDock.tsx'
import {QueuedInputEditor} from '/src/components/chat/QueuedInputEditor.tsx'
import {QueuedFollowups} from '/src/components/chat/QueuedFollowups.tsx'
import {TaskQueue} from '/src/components/inspector/TaskQueue.tsx'
import {TerminalSetupPanel} from '/src/components/inspector/TerminalSetupPanel.tsx'
import {useChatStore,emptySession} from '/src/store/chat.ts'
import {useQueueEditStore} from '/src/store/queueEdits.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
const view=new URLSearchParams(location.search).get('view')
const queued={id:'entry',kind:'runs',text:'Original input 原文',edit_version:'a'.repeat(64)}
useChatStore.setState({sessions:{fixture:{...emptySession(),connection:'open',queued:1}}})
if(['editor','corrupt'].includes(view))useQueueEditStore.getState().setEdit('fixture',{entry:queued,draft:'Edited input 原文'})
const noop=()=>{}
const views={corrupt:[QueuedInputEditor,{sessionId:'fixture',entries:[queued],ready:true,disabled:false,onWorking:noop,onChanged:noop,onClosed:noop}],control:[QueueControlDock,{sessionId:'fixture'}],editor:[QueuedInputEditor,{sessionId:'fixture',entries:[queued],ready:true,disabled:false,onWorking:noop,onChanged:noop,onClosed:noop}],followups:[QueuedFollowups,{sessionId:'fixture',disabled:false,preserveDraft:true,onRestored:noop,onWorking:noop}],queue:[TaskQueue,{sessionId:'fixture'}],setup:[TerminalSetupPanel,{active:true,enabled:true,onReady:noop}]}
const [Component,props]=views[view]
createRoot(document.getElementById('root')).render(React.createElement(Component,props))
`

await browserScenario(async ({server,browser}) => {
  for (const view of ['control','editor','followups','queue','setup','corrupt']) {
    const page = await browser.newPage()
    page.setDefaultTimeout(15000)
    await page.clock.install({time:new Date(0)})
    await page.clock.pauseAt(new Date(86400000))
    const requests = [], errors = []
    page.on('pageerror',error=>errors.push(error.message))
    await page.route('**/api/v1/**',route=>{
      requests.push({method:route.request().method(),url:route.request().url()})
      return route.fulfill({status:200,body:'not json',contentType:'application/json'})
    })
    if(view==='corrupt')await page.addInitScript(()=>sessionStorage.setItem('harness.queue-edits.v1','Original damaged queue draft'))
    await page.goto(server.resolvedUrls.local[0]+'__queue-error?view='+view)
    const action = view==='control' ? 'ui.components.chat.QueueControlDock.e90e4d4b3a66' : view==='editor' ? 'ui.components.chat.QueuedInputEditor.756b121cc6b1' : null
    if(action) await page.getByRole('button',{name:await page.evaluate(key=>window.copy(key),action),exact:true}).click()
    const expected = () => page.evaluate(view=>{
      if(view==='corrupt')return window.copy('ui.queueDrafts.invalidStorage')
      const invalid=window.copy('ui.http.invalidResponse')
      if(view==='control')return window.copy('ui.lib.queueControl.96befea94f27')
      if(view==='editor')return window.copy('ui.lib.queueEdits.ca71bc9168b4')
      if(view==='followups')return window.copy('ui.components.chat.QueuedFollowups.82fe2ddfd2ce',{detail:invalid})
      if(view==='queue')return window.copy('ui.queue.readFailed',{detail:invalid})
      return invalid
    },view)
    await page.getByText(await expected(),{exact:true}).waitFor()
    const before=requests.length
    for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
      await page.setViewportSize({width,height:900})
      await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
      await page.getByText(await expected(),{exact:true}).waitFor()
      assert.equal(requests.length,before,view+': language switch repeated an operation')
      if(['editor','corrupt'].includes(view))assert.equal(await page.getByRole('textbox').inputValue(),'Edited input 原文')
    }
    if(action){
      assert.equal(requests.filter(request=>request.method==='PATCH').length,1)
      assert.equal(requests.filter(request=>request.method==='GET').length,1)
    }
    if(view==='corrupt'){assert.equal(requests.length,0);assert.equal(await page.evaluate(()=>sessionStorage.getItem('harness.queue-edits.v1')),'Original damaged queue draft')}
    assert.deepEqual(errors,[],view)
    await page.close()
    console.log(view+': error language updates without replaying writes or changing drafts')
  }
},{entry:'/__queue-error-entry.js',vite:{plugins:[{
  name:'queue-error-language-fixture',resolveId(id){if(id==='/__queue-error-entry.js')return id},load(id){if(id==='/__queue-error-entry.js')return entry},
  configureServer(server){server.middlewares.use('/__queue-error',async(_req,res,next)=>{
    try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__queue-error','<html><body><div id="root"></div><script type="module" src="/__queue-error-entry.js"></script></body></html>'))}catch(error){next(error)}
  })},
}]}})
