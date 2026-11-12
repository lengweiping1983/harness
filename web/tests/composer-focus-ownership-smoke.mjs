import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {useComposerEditor} from '/src/hooks/useComposerEditor.ts'
import {useAppStore} from '/src/store/app.ts'
import '/src/styles.css'
useAppStore.setState({activeSessionId:'A'})
function Editor(){const id=useAppStore(state=>state.activeSessionId);const [enabled,setEnabled]=useState(true);window.disable=()=>setEnabled(false);const editor=useComposerEditor('',id,enabled);window.queueFocus=()=>editor.scheduleFrame(()=>editor.taRef.current?.focus());window.capture=()=>{const schedule=editor.captureFrames();window.finish=()=>schedule(()=>editor.taRef.current?.focus())};return React.createElement('textarea',{ref:editor.taRef,'aria-label':'Editor'})}
function Fixture(){const [visible,setVisible]=useState(true);window.hide=()=>setVisible(false);window.switchTo=id=>useAppStore.setState({activeSessionId:id});return React.createElement(React.Fragment,null,React.createElement('input',{'aria-label':'Other'}),visible?React.createElement(Editor):null)}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const delayed of [false,true])for(const mode of ['normal','switch','roundtrip','unmount','disabled']){
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__model-width')
 await page.getByRole('textbox',{name:'Editor'}).waitFor()
 await page.getByRole('textbox',{name:'Other',exact:true}).focus()
 await page.evaluate(({mode,delayed})=>{
  if(delayed)window.capture();else window.queueFocus()
  if(mode==='switch')window.switchTo('B')
  if(mode==='roundtrip'){window.switchTo('B');window.switchTo('A')}
  if(mode==='unmount')window.hide()
  if(mode==='disabled')window.disable()
 },{mode,delayed})
 if(delayed)await page.evaluate(()=>window.finish())
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
 assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('aria-label')),mode==='normal'?'Editor':'Other')
 assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Composer editor frames: normal focus works; switch, batched A-B-A and unmount cancel old focus')
},{entry:'/__model-width-entry.js',vite:{plugins:[{name:'model-width',resolveId(id){if(id==='/__model-width-entry.js')return id},load(id){if(id==='/__model-width-entry.js')return entry},configureServer(server){server.middlewares.use('/__model-width',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__model-width','<html><body><div id="root"></div><script type="module" src="/__model-width-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
