import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Conversation} from '/src/components/chat/Conversation.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale
useAppStore.setState({activeSessionId:'fixture'})
const blocks=Array.from({length:40},(_,index)=>({id:'message-'+index,kind:'user',at:1,text:'Original message '+index+'\\n'+('Original 原文 '.repeat(30))}))
createRoot(document.getElementById('root')).render(React.createElement('div',{style:{height:500,display:'flex'}},React.createElement(Conversation,{blocks,running:false,streaming:false,connection:'open',readOnly:true})))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__conversation-scroll-ime')
 const scroll=page.locator('.conversation-scroll');await scroll.waitFor()
 for(const locale of ['en','zh-CN','en'])for(const [dark,width] of [[false,390],[true,1440]]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  await scroll.evaluate(node=>{node.dispatchEvent(new WheelEvent('wheel',{bubbles:true,deltaY:-1}));node.scrollTop=400;node.dispatchEvent(new Event('scroll',{bubbles:true}))})
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
  for(const key of ['Home','PageUp','ArrowUp'])for(const props of [{isComposing:true},{keyCode:229}]){
   const result=await scroll.evaluate((node,{key,props})=>{const before=node.scrollTop;const event=new KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,...props});node.dispatchEvent(event);return {before,after:node.scrollTop,prevented:event.defaultPrevented}},{key,props})
   assert.equal(result.prevented,false,'Composition must not trigger history navigation')
   assert.equal(result.after,result.before)
  }
  const prevented=await scroll.evaluate(node=>{const event=new KeyboardEvent('keydown',{key:'Home',bubbles:true,cancelable:true});node.dispatchEvent(event);return event.defaultPrevented})
  assert.equal(prevented,true)
  await page.waitForFunction(()=>document.querySelector('.conversation-scroll').scrollTop===0)
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Conversation scroll: composing/229 Home/PageUp/ArrowUp preserved, normal Home works across languages and layouts')
},{entry:'/__conversation-scroll-ime-entry.js',vite:{plugins:[{name:'conversation-scroll-ime-fixture',resolveId(id){if(id==='/__conversation-scroll-ime-entry.js')return id},load(id){if(id==='/__conversation-scroll-ime-entry.js')return entry},configureServer(server){server.middlewares.use('/__conversation-scroll-ime',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__conversation-scroll-ime','<html><body><div id="root"></div><script type="module" src="/__conversation-scroll-ime-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
