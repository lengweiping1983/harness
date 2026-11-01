import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {BrowserTabs} from '/src/components/ui/BrowserTabs.tsx'
import {setLocale,t,useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale
const labels={context:'ui.components.inspector.Inspector.6fc38ef3602f',todos:'ui.inspector.plansAndTodos',files:'ui.components.inspector.Inspector.39932f24fe11'}
function Fixture(){
 useLocale()
 const [selected,select]=useState('todos'),[tabs,setTabs]=useState(['context','todos','files'])
 window.reset=()=>{select('todos');setTabs(['context','todos','files'])}
 return React.createElement('main',{className:'w-full max-w-2xl bg-surface text-ink'},
  React.createElement(BrowserTabs,{value:selected,label:t('ui.components.inspector.Inspector.fc48a7f2334a'),panelId:'panel',
   options:tabs.map(value=>({value,label:t(labels[value]),closeLabel:t('ui.deferred.close')+' '+t(labels[value])})),
   onChange:select,onClose:value=>{const index=tabs.indexOf(value);setTabs(tabs.filter(tab=>tab!==value));if(selected===value)select(tabs[index+1]??tabs[index-1])}}),
  React.createElement('div',{id:'panel',role:'tabpanel'},selected))
}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__tabs-ime')
 const selected=page.getByRole('tab',{selected:true})
 const panel=page.getByRole('tabpanel')
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark);window.reset()},{locale,dark})
  await page.getByRole('tab',{name:locale==='en'?'Plans and to-dos':'计划与执行清单',exact:true}).waitFor()
  await selected.focus()
  for(const key of ['Home','End','ArrowLeft','ArrowRight','Delete']){
   for(const composition of [{isComposing:true},{keyCode:229}]){
    await selected.dispatchEvent('keydown',{key,bubbles:true,cancelable:true,...composition})
    await page.evaluate(()=>new Promise(requestAnimationFrame))
    assert.equal(await panel.innerText(),'todos',JSON.stringify({key,composition}))
    assert.equal(await page.getByRole('tab').count(),3,'Composition must not close a tab')
    assert.ok(await selected.evaluate(node=>node===document.activeElement),'Composition must preserve focus')
   }
  }
  await selected.press('Home')
  await page.waitForFunction(()=>document.getElementById('panel').textContent==='context')
  await selected.press('End')
  await page.waitForFunction(()=>document.getElementById('panel').textContent==='files')
  await selected.press('ArrowLeft')
  await page.waitForFunction(()=>document.getElementById('panel').textContent==='todos')
  await selected.press('Delete')
  await page.waitForFunction(()=>document.querySelectorAll('[role="tab"]').length===2)
  assert.equal(await panel.innerText(),'files','Ordinary Delete must still select the surviving neighbour')
  assert.ok(await selected.evaluate(node=>node===document.activeElement),'Ordinary Delete must focus the surviving neighbour')
 }
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Workspace tabs: IME and keyCode 229 preserve selection/count/focus; ordinary navigation and Delete; en→zh→en, themes and widths passed')
},{entry:'/__tabs-ime-entry.js',vite:{plugins:[{
 name:'tabs-ime-fixture',resolveId(id){if(id==='/__tabs-ime-entry.js')return id},load(id){if(id==='/__tabs-ime-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__tabs-ime',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__tabs-ime','<html><head><link rel="icon" href="data:,"></head><body><div id="root"></div><script type="module" src="/__tabs-ime-entry.js"></script></body></html>'))}catch(error){next(error)}})},
}]}})
