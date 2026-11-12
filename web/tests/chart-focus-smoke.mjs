import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Overlay} from '/src/components/ui/primitives.tsx'
import {LiveChart} from '/src/components/markdown/LiveChart.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t
function Fixture(){const [open,setOpen]=useState(false);return React.createElement(React.Fragment,null,React.createElement('button',{'data-open':true,onClick:()=>setOpen(true)},'Open'),open?React.createElement(Overlay,{onClose:()=>setOpen(false)},React.createElement('button',{'data-parent':true},'Parent'),React.createElement(LiveChart,{lang:'svg',code:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><circle cx="50" cy="50" r="20"/></svg>'}),React.createElement(LiveChart,{lang:'mermaid',code:'graph TD; A-->B'})):null)}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const locale of ['en','zh-CN'])for(const kind of ['SVG','Mermaid']){
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.goto(server.resolvedUrls.local[0]+'__chart-focus');await page.evaluate(locale=>window.locale(locale),locale)
  await page.locator('[data-open]').click()
  await page.locator('.mermaid-host svg').waitFor()
  const figure=page.locator('figure').nth(kind==='SVG'?0:1)
  const prefix=kind==='SVG'?'LiveChart':'Mermaid'
  const expand=await page.evaluate(prefix=>window.copy('ui.components.markdown.'+prefix+'.80f8fbcfa011'),prefix)
  await figure.getByRole('button',{name:expand,exact:true}).click()
  const dialog=page.getByRole('dialog',{name:kind,exact:true});await dialog.waitFor()
  await dialog.locator('button').last().focus();await page.keyboard.press('Tab')
  assert.equal(await dialog.evaluate(node=>node.contains(document.activeElement)),true)
  await dialog.locator('button').first().focus();await page.keyboard.press('Shift+Tab')
  assert.equal(await dialog.evaluate(node=>node.contains(document.activeElement)),true)
  await page.evaluate(()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',isComposing:true,bubbles:true,cancelable:true})))
  await dialog.waitFor();await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'})
  assert.equal(await page.getByRole('dialog').count(),1)
  assert.equal(await page.evaluate(()=>document.body.style.overflow),'hidden')
  assert.equal(await figure.getByRole('button',{name:expand,exact:true}).evaluate(node=>node===document.activeElement),true)
  await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'detached'})
  assert.equal(await page.evaluate(()=>document.body.style.overflow),'')
  assert.equal(await page.locator('[data-open]').evaluate(node=>node===document.activeElement),true)
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Expanded SVG/Mermaid own Tab, Escape and scroll locks inside a parent Overlay in both languages')
},{entry:'/__chart-focus-entry.js',vite:{plugins:[{name:'chart-focus-fixture',resolveId(id){if(id==='/__chart-focus-entry.js')return id},load(id){if(id==='/__chart-focus-entry.js')return entry},configureServer(server){server.middlewares.use('/__chart-focus',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__chart-focus','<html><body><div id="root"></div><script type="module" src="/__chart-focus-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
