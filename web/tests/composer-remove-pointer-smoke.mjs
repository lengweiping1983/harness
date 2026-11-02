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
window.copy=t;window.locale=setLocale
useAppStore.setState({activeSessionId:null})
window.restore=()=>useDraftStore.getState().setAgentSelection('__unsaved__',{name:'original',display_name:'Original Agent',agent_type:'agent'})
window.selected=()=>useDraftStore.getState().bySession.__unsaved__?.agentSelection
window.restore()
createRoot(document.getElementById('root')).render(React.createElement('div',{className:'app-shell',style:{height:'100dvh',display:'flex'}},React.createElement(ConversationPane,{title:'Original',empty:true},React.createElement(Composer,{running:false,queued:0,planMode:false,connection:'open',onSend:()=>{},onCancel:()=>{},onTogglePlan:()=>{}}))))
`
await browserScenario(async({server,browser})=>{
 for(const language of ['en','zh-CN'])for(const width of [390,1440])for(const size of [10,16,22]){
  const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.route('**/api/v1/**',route=>route.fulfill({status:503,json:{message:'Fixture unavailable'}}))
  await page.goto(server.resolvedUrls.local[0]+'__composer-remove')
  await page.evaluate(({language,size})=>{window.locale(language);document.documentElement.style.fontSize=size+'px'},{language,size})
  const remove=page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.composerAgents.unmount')),exact:true})
  await remove.waitFor();await remove.locator('..').hover()
  assert.equal(await remove.evaluate(node=>{const r=node.getBoundingClientRect();return node.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2))}),true)
  await remove.click();assert.equal(await page.evaluate(()=>window.selected()),undefined)
  // 移除后焦点在下一帧才交还编辑器。等这一帧跑完再开始键盘步骤，否则 Enter 会落到编辑器上。
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
  await page.evaluate(()=>window.restore());await remove.waitFor();await remove.focus();await remove.press('Enter')
  assert.equal(await page.evaluate(()=>window.selected()),undefined)
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Actual Composer agent removal is pointer reachable on parent hover and keyboard reachable across languages, widths and font sizes')
},{entry:'/__composer-remove-entry.js',vite:{plugins:[{name:'composer-remove-fixture',resolveId(id){if(id==='/__composer-remove-entry.js')return id},load(id){if(id==='/__composer-remove-entry.js')return entry},configureServer(server){server.middlewares.use('/__composer-remove',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__composer-remove','<html><body><div id="root"></div><script type="module" src="/__composer-remove-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
