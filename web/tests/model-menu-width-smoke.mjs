import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useRef,useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ModelMenu} from '/src/components/chat/ModelPicker.tsx'
import {useAnchoredMenu} from '/src/hooks/useAnchoredMenu.ts'
import {api} from '/src/lib/api.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale
window.scope=scope=>useAppStore.setState({activeSessionId:scope==='default'?null:'one',sessions:[{id:'one',model:scope==='override'?'fixture':null}]})
window.scope('default')
api.connection=async()=>({ok:true,data:{version:2,revision:'fixture',providers:[],models:[],current:null,cycle:1}})
function Fixture(){const anchor=useRef(null);const [open,setOpen]=useState(true);window.closeMenu=()=>setOpen(false);const style=useAnchoredMenu(anchor,open,'model');return React.createElement(React.Fragment,null,React.createElement('button',{ref:anchor,style:{position:'fixed',right:16,bottom:80}},'Anchor'),open?React.createElement('div',{'data-position':true,style},React.createElement(ModelMenu,{models:[{id:'fixture',label:'Original model',provider:'fixture',visible:true}],current:'fixture',onClose:()=>{}})):null)}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/connection',route=>route.fulfill({json:{version:2,revision:'fixture',providers:[],models:[],current:null,cycle:1}}))
 await page.goto(server.resolvedUrls.local[0]+'__model-width')
 for(const scope of ['default','override','inherited'])for(const language of ['en','zh-CN'])for(const width of [390,1440])for(const font of [10,16,22]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({scope,language,font})=>{window.scope(scope);window.locale(language);document.documentElement.style.fontSize=font+'px'},{scope,language,font})
  const expected=Math.min(21*font,width-24)
  await page.waitForFunction(expected=>{const parent=document.querySelector('[data-position]');return parent&&Math.abs(parent.getBoundingClientRect().width-expected)<1},expected,{timeout:3000})
  const boxes=await page.locator('[data-position]').evaluate(node=>({parent:node.getBoundingClientRect().toJSON(),child:node.firstElementChild.getBoundingClientRect().toJSON()}))
  assert.ok(Math.abs(boxes.child.width-boxes.parent.width)<1)
  assert.ok(boxes.parent.left>=11&&boxes.parent.right<=width-11)
  assert.ok(boxes.parent.top>=11&&boxes.parent.bottom<=889,'The anchored menu must stay within the viewport')
  await page.locator('footer').scrollIntoViewIfNeeded()
  assert.equal(await page.locator('footer').isVisible(),true)
 }
 await page.evaluate(()=>window.closeMenu())
 await page.locator('[data-position]').waitFor({state:'detached'})
 assert.equal(await page.locator('.w-model-menu').count(),0)
 assert.deepEqual(errors,[]);await page.close();console.log('Model menu width follows its actual rem token at 10/16/22px across two languages and viewport widths')
},{entry:'/__model-width-entry.js',vite:{plugins:[{name:'model-width',resolveId(id){if(id==='/__model-width-entry.js')return id},load(id){if(id==='/__model-width-entry.js')return entry},configureServer(server){server.middlewares.use('/__model-width',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__model-width','<html><body><div id="root"></div><script type="module" src="/__model-width-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
