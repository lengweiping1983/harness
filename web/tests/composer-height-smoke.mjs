import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Composer} from '/src/components/chat/Composer.tsx'
import {ConversationPane} from '/src/components/chat/ConversationPane.tsx'
import {useDraftStore} from '/src/store/drafts.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.copy=t
const root=createRoot(document.getElementById('root'))
window.configure=({language,size,running,empty,compact=false})=>{
 setLocale(language);document.documentElement.style.fontSize=size+'px'
 useAppStore.setState({activeSessionId:null})
 useDraftStore.getState().setText('__unsaved__',running||compact?'':'Original 原文\\n'.repeat(100))
 if(!running&&!compact)useDraftStore.setState(state=>({bySession:{...state.bySession,__unsaved__:{...state.bySession.__unsaved__,attachments:Array.from({length:20},(_,i)=>({localId:'file-'+i,name:'Original-'+i+'.txt',mediaType:'text/plain',size:12,isImage:false,attachmentId:'fixture-'+i}))}}}))
 root.render(React.createElement('div',{className:'app-shell',style:{height:'100dvh',display:'flex'}},React.createElement(ConversationPane,{title:'Original',empty},
  React.createElement('div',{style:{flex:'1 1 0',minHeight:0,overflow:'auto'}},'Original transcript'),
  React.createElement(Composer,{running,queued:0,planMode:false,connection:'open',onSend:()=>{window.sent=true},onCancel:()=>{window.stopped=true},onTogglePlan:()=>{},todos:Array.from({length:compact?0:20},(_,i)=>({content:'Original task '+i,status:'pending'}))}))))
}
`
await browserScenario(async({server,browser})=>{
 for(const theme of ['light','dark'])for(const width of [390,1440]){
  const page=await browser.newPage({viewport:{width,height:900}})
  await page.route('**/api/v1/**',route=>route.fulfill({status:503,json:{message:'Fixture unavailable'}}))
  await page.goto(server.resolvedUrls.local[0]+'__composer-height')
  for(const language of ['en','zh-CN','en']){
  await page.evaluate(config=>{document.documentElement.classList.toggle('dark',config.theme==='dark');window.configure(config)},{language,theme,size:14,running:false,empty:false,compact:true})
  const card=page.locator('.composer-card');await card.waitFor()
  const gap=await page.locator('.composer-dock').evaluate(node=>innerHeight-node.getBoundingClientRect().bottom)
  assert.ok(gap>=0&&gap<=9,`Composer bottom gap ${gap}: ${language} ${theme} ${width}`)
  const project=page.locator('.composer-dock [aria-label]').filter({has:page.getByRole('button',{name:language==='en'?'Choose a project':'选择项目',exact:true})})
  assert.ok(await project.evaluate(node=>node.getBoundingClientRect().top>=node.closest('.composer-dock').querySelector('.composer-card').getBoundingClientRect().bottom),'Project settings follow the editor without overlapping it')
  assert.equal(await page.locator('.composer-help').count(),0)
  }
  await page.close()
 }
 for(const empty of [false,true])for(const height of [160,240])for(const language of ['en','zh-CN'])for(const width of [390,1440])for(const size of [10,22])for(const running of [false,true]){
  const page=await browser.newPage({viewport:{width,height}}),errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.route('**/api/v1/**',route=>route.fulfill({status:503,json:{message:'Fixture unavailable'}}))
  await page.goto(server.resolvedUrls.local[0]+'__composer-height')
  await page.evaluate(config=>window.configure(config),{language,size,running,empty})
  const dock=page.locator('.composer-dock');await dock.waitFor()
  const input=dock.locator('textarea');await input.waitFor()
  await dock.locator('details').first().evaluate(node=>node.open=true)
  const action=dock.getByRole('button',{name:await page.evaluate(running=>window.copy(running?'ui.components.chat.Composer.ca4d973c0b00':'ui.components.chat.Composer.edecf0ae6e51'),running),exact:true})
  for(const node of [input,action]){
   await node.scrollIntoViewIfNeeded()
   assert.equal(await node.evaluate(node=>{const r=node.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;return y>=0&&y<innerHeight&&node.contains(document.elementFromPoint(x,y))}),true,language+' '+width+' '+size+' '+running+' '+height+' '+empty)
  }
  if(running){
   const draft='Continue original work 原文'
   await input.fill(draft)
   const queue=dock.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.composerSteering.queue')),exact:true})
   assert.equal(await queue.isEnabled(),true)
   await input.evaluate(element=>{element.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));element.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,bubbles:true}))})
   assert.equal(await page.evaluate(()=>Boolean(window.sent)),false)
   await input.evaluate(element=>element.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})))
   await action.click()
   assert.equal(await page.evaluate(()=>window.stopped),true)
   assert.equal(await input.inputValue(),draft,'Stopping preserves a composed follow-up')
   await queue.click()
   assert.equal(await page.evaluate(()=>window.sent),true)
  }
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Actual Composer editor and send/stop controls reachable in 64 short viewport configurations')
},{entry:'/__composer-height-entry.js',vite:{plugins:[{name:'composer-height-fixture',resolveId(id){if(id==='/__composer-height-entry.js')return id},load(id){if(id==='/__composer-height-entry.js')return entry},configureServer(server){server.middlewares.use('/__composer-height',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__composer-height','<html><body><div id="root"></div><script type="module" src="/__composer-height-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
