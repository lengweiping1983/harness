import assert from 'node:assert/strict'
import {spawnSync} from 'node:child_process'
import {mkdir,readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const result = spawnSync(fixturePython, ['-m', 'tests.widget_visual_fixture'], {cwd:new URL('../..',import.meta.url),encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'}})
assert.equal(result.status,0,result.stderr)
const fixture = JSON.parse(result.stdout)
assert.equal(fixture.reason,'completed',fixture.error)
const entry = `
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ToolBlock} from '/src/components/chat/blocks/ToolBlock.tsx'
import {Markdown} from '/src/components/markdown/Markdown.tsx'
import {HtmlSandboxFrame} from '/src/components/ui/HtmlSandboxFrame.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import {useAppStore} from '/src/store/app.ts'
import '/src/styles.css'
const data=${JSON.stringify(fixture)}
window.locale=setLocale;window.copy=t;window.owner=id=>useAppStore.setState({activeSessionId:id})
const rows=data.tools.filter(row=>row.display?.widget)
function App(){const [broken,setBroken]=useState(true);window.fixScript=()=>setBroken(false);const [live,setLive]=useState('\\x60\\x60\\x60svg\\n<svg viewBox="0 0 100 60"><rect width="20" height="20"/>');window.live=setLive
return React.createElement('main',{style:{maxWidth:1000,margin:'auto',padding:16}},
React.createElement('section',{id:'live'},React.createElement(Markdown,{streaming:true},live)),
...rows.concat([{...rows[0],call_id:'second-svg',display:{widget:{...rows[0].display.widget,title:'Second SVG'}}}]).map((row,index)=>React.createElement(ToolBlock,{key:row.call_id,block:{kind:'tool',id:row.call_id,callId:row.call_id,name:'show_widget',status:'ok',at:index,display:row.display,output:row.results[0].content}})),
React.createElement('section',{id:'error-demo'},React.createElement(HtmlSandboxFrame,{title:'Error demo',code:broken?'<p>Original fallback</p><script>throw Error("Demo failure")</script>':'<p>Recovered</p>'})),
React.createElement('section',{id:'markdown-html'},React.createElement(Markdown,null,'\\x60\\x60\\x60html\\n'+data.html+'\\n\\x60\\x60\\x60')))}
setLocale('en');createRoot(document.getElementById('root')).render(React.createElement(App))
`
const html='<html><body><div id="root"></div><script type="module" src="/__visualization-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage()
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.addInitScript(()=>{window.messages=[];addEventListener('message',event=>{if(event.data?.channel==='harness-widget')window.messages.push({id:event.data.id,kind:event.data.kind,title:[...document.querySelectorAll('iframe')].find(frame=>frame.contentWindow===event.source)?.title})})})
 await page.goto(server.resolvedUrls.local[0]+'__visualization')
 const frame=page.frameLocator('iframe[title="HTML demo"]')
 await page.locator('iframe[title="HTML demo"]').scrollIntoViewIfNeeded()
 try{await page.locator('[aria-busy="false"]:has(> iframe[title="HTML demo"])').waitFor()}catch(error){console.log('Handshake diagnostics',await page.evaluate(()=>({messages:window.messages.slice(-30),frames:[...document.querySelectorAll('iframe')].map(frame=>({title:frame.title,busy:frame.parentElement.getAttribute('aria-busy'),id:/const id="([^"]+)"/.exec(frame.srcdoc)?.[1],body:frame.parentElement.innerText}))})),errors);throw error}
 await frame.getByText('Ready',{exact:true}).waitFor()
 await frame.getByRole('button',{name:'运行模拟 · 0',exact:true}).click()
 await frame.getByRole('button',{name:'运行模拟 · 1',exact:true}).waitFor()
 await frame.locator('#workers').evaluate(node=>{node.value='8';node.dispatchEvent(new Event('input',{bubbles:true}))})
 assert.equal(await frame.locator('#queued').textContent(),'16')
 const card=page.locator('figure').filter({has:page.locator('iframe[title="HTML demo"]')})
 await card.getByRole('button',{name:'View code',exact:true}).click()
 await card.getByRole('button',{name:'View rendering',exact:true}).click()
 await page.locator('[aria-busy="false"]:has(> iframe[title="HTML demo"])').waitFor()
 await frame.getByRole('button',{name:'运行模拟 · 1',exact:true}).waitFor()
 await card.getByRole('button',{name:'Enlarge',exact:true}).click()
 await page.getByRole('dialog',{name:'HTML demo',exact:true}).waitFor()
 await page.locator('[aria-busy="false"]:has(> iframe[title="HTML demo"])').waitFor()
 await frame.getByRole('button',{name:'运行模拟 · 1',exact:true}).click()
 await frame.getByRole('button',{name:'运行模拟 · 2',exact:true}).waitFor()
 await page.keyboard.press('Tab')
 await page.waitForFunction(()=>document.querySelector('[role="dialog"] figcaption button')===document.activeElement)
 await frame.locator('#workers').focus()
 await page.keyboard.press('Shift+Tab')
 await page.waitForFunction(()=>[...document.querySelectorAll('[role="dialog"] figcaption button')].at(-1)===document.activeElement)
 await frame.getByRole('button',{name:'运行模拟 · 2',exact:true}).focus()
 await page.keyboard.press('Escape')
 await page.getByRole('dialog',{name:'HTML demo',exact:true}).waitFor({state:'detached'})
 assert.equal(await frame.locator('html').evaluate(()=>{try{return !!parent.document}catch{return false}}),false)
 assert.equal(await frame.locator('html').evaluate(()=>{try{localStorage.getItem('x');return true}catch{return false}}),false)
 await page.locator('#error-demo [role="alert"]').getByText('Demo failure',{exact:false}).waitFor()
 await page.locator('#error-demo').getByRole('button',{name:'Run again',exact:true}).click()
 await page.locator('#error-demo [role="alert"]').getByText('Demo failure',{exact:false}).waitFor()
 await page.evaluate(()=>{const f=document.querySelector('#error-demo iframe');window.oldFrameWindow=f.contentWindow;window.oldIdentity=/const id="([^"]+)"/.exec(f.srcdoc)[1];window.fixScript()})
 await page.locator('#error-demo [role="alert"]').waitFor({state:'detached'})
 await page.frameLocator('iframe[title="Error demo"]').getByText('Recovered',{exact:true}).waitFor()
 await page.locator('#error-demo iframe').scrollIntoViewIfNeeded()
 await page.locator('#error-demo [aria-busy="false"]').waitFor()
 await page.evaluate(()=>dispatchEvent(new MessageEvent('message',{source:window.oldFrameWindow,data:{channel:'harness-widget',id:window.oldIdentity,kind:'error',message:'obsolete failure'}})))
 assert.equal(await page.locator('#error-demo [role="alert"]').count(),0)
 await page.locator('#markdown-html iframe').waitFor()
 await page.locator('#markdown-html iframe').scrollIntoViewIfNeeded()
 await page.locator('#markdown-html [aria-busy="false"]').waitFor()
 await page.frameLocator('#markdown-html iframe').locator('body').evaluate(()=>{window.actions=[];for(const name of ['pointerdown','pointerup','click'])document.addEventListener(name,e=>window.actions.push({type:name,target:e.target.id,x:e.clientX,y:e.clientY}),true)})
 await page.frameLocator('#markdown-html iframe').getByRole('button',{name:'运行模拟 · 0',exact:true}).click()
 try{await page.frameLocator('#markdown-html iframe').getByRole('button',{name:'运行模拟 · 1',exact:true}).waitFor({timeout:5000})}catch(error){console.log('Interaction diagnostics',await page.frameLocator('#markdown-html iframe').locator('body').evaluate(()=>({actions:window.actions,text:document.getElementById('counter').textContent,handler:typeof document.getElementById('counter').onclick})),await page.locator('#markdown-html iframe').evaluate(node=>({pointer:getComputedStyle(node).pointerEvents,rect:node.getBoundingClientRect().toJSON(),busy:node.parentElement.getAttribute('aria-busy')})));throw error}
 await page.locator('#live svg[viewBox="0 0 100 60"] rect').evaluate(node=>window.firstRect=node)
 await page.evaluate(()=>window.live('```svg\n<svg viewBox="0 0 100 60"><rect width="20" height="20"/><circle cx="50" cy="30" r="10"/>'))
 await page.locator('#live svg[viewBox="0 0 100 60"] circle').waitFor()
 assert.equal(await page.locator('#live svg[viewBox="0 0 100 60"] rect').evaluate(node=>node===window.firstRect),true)
 await page.evaluate(()=>window.live('```svg\n<svg viewBox="0 0 100 60"><rect width="20" height="20"/><circle cx="50" cy="30" r="10"/></svg>\n```'))
 await page.locator('#live svg[viewBox="0 0 100 60"] circle').waitFor()
 assert.equal(await page.evaluate(()=>{const ids=[...document.querySelectorAll('.widget-host svg [id]')].map(n=>n.id);return new Set(ids).size===ids.length}),true)
 const svgCard=page.locator('figure').filter({has:page.locator('.widget-host')}).filter({hasText:'SVG diagram'})
 const pngPending=page.waitForEvent('download')
 await svgCard.getByRole('button',{name:'Save as image',exact:true}).click()
 const png=await pngPending
 assert.equal(png.suggestedFilename(),'SVG_diagram.png')
 await mkdir('../output/visualization',{recursive:true})
 await png.saveAs('../output/visualization/exported.png')
 const pngBytes=await readFile('../output/visualization/exported.png')
 assert.equal(pngBytes.readUInt32BE(16),720)
 assert.equal(pngBytes.readUInt32BE(20),240)
 const svgPending=page.waitForEvent('download')
 await svgCard.getByRole('button',{name:'Download',exact:true}).click()
 const svgDownload=await svgPending
 assert.equal(svgDownload.suggestedFilename(),'SVG_diagram.svg')
 await svgDownload.saveAs('../output/visualization/exported.svg')
 const oldHeight=await page.locator('iframe[title="HTML demo"]').evaluate(n=>n.style.height)
 await page.evaluate(()=>{const host=document.querySelector('iframe[title="HTML demo"]');const other=document.querySelector('#markdown-html iframe');const id=/const id="([^"]+)"/.exec(host.srcdoc)[1];dispatchEvent(new MessageEvent('message',{source:other.contentWindow,data:{channel:'harness-widget',id,kind:'resize',height:99999}}))})
 assert.equal(await page.locator('iframe[title="HTML demo"]').evaluate(n=>n.style.height),oldHeight)
 for(const language of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({language,dark})=>{window.locale(language);document.documentElement.classList.toggle('dark',dark)},{language,dark})
  await frame.locator('html').evaluate((node,dark)=>new Promise(resolve=>{if(node.classList.contains('dark')===dark){resolve();return}const o=new MutationObserver(()=>{if(node.classList.contains('dark')===dark){o.disconnect();resolve()}});o.observe(node,{attributes:true})}),dark)
  await frame.getByRole('button',{name:'运行模拟 · 2',exact:true}).waitFor()
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
 }
 await page.setViewportSize({width:1200,height:1000})
 const darkPngPending=page.waitForEvent('download')
 await svgCard.getByRole('button',{name:'Save as image',exact:true}).click()
 await (await darkPngPending).saveAs('../output/visualization/exported-dark.png')
 await mkdir('../output/visualization',{recursive:true})
 await card.scrollIntoViewIfNeeded()
 await page.screenshot({path:'../output/visualization/conversation.png',fullPage:true})
 await card.screenshot({path:'../output/visualization/interactive-card.png'})
 await page.evaluate(()=>window.owner('other-conversation'))
 await frame.getByRole('button',{name:'运行模拟 · 0',exact:true}).waitFor()
 assert.equal(await frame.locator('#workers').inputValue(),'4')
 assert.deepEqual(errors.filter(message=>!message.includes('Demo failure')),[])
 console.log('Visualizations: native tool correction, inline SVG/HTML, progressive SVG, isolated IDs, preserved interactions, source/fullscreen, theme/language/width and message ownership passed')
},{entry:'/__visualization-entry.js',vite:{plugins:[{name:'visualization-fixture',resolveId(id){if(id==='/__visualization-entry.js')return id},load(id){if(id==='/__visualization-entry.js')return entry},configureServer(server){server.middlewares.use('/__visualization',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__visualization',html))}catch(error){next(error)}})}}]}})
