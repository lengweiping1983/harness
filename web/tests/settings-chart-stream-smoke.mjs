import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {AppOverlays} from '/src/components/overlays/AppOverlays.tsx'
import {LiveChart} from '/src/components/markdown/LiveChart.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t
function Fixture(){
 const [version,setVersion]=useState(0),[closed,setClosed]=useState(false)
 window.update=(version,closed=false)=>{setVersion(version);setClosed(closed)}
 window.openSettings=()=>useAppStore.getState().setSettingsOpen(true)
 const code='<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><text x="10" y="30">Version '+version+'</text>'+Array.from({length:200},(_,i)=>'<rect x="'+i+'" y="50" width="1" height="'+version+'"/>').join('')+'</svg>'
 return React.createElement(React.Fragment,null,React.createElement('textarea',{'aria-label':'Draft',defaultValue:'保留草稿'}),React.createElement(LiveChart,{lang:'svg',code,closed}),React.createElement('output',{'data-source-state':true},version+':'+closed),React.createElement(AppOverlays))
}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`

await browserScenario(async ({server,browser}) => {
 const page = await browser.newPage(), errors = []
 page.on('pageerror', error => errors.push(error.message))
 await page.clock.install()
 await page.goto(server.resolvedUrls.local[0]+'__settings-chart-stream')
 await page.locator('figure svg text').filter({hasText:'Version 0'}).waitFor()
 for (const locale of ['en','zh-CN','en']) for (const theme of ['light','dark']) for (const width of [390,1440]) {
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
  await page.evaluate(()=>window.openSettings())
  await page.locator('#language-preference').waitFor({timeout:2000})
  // 以真实 DOM 观察验证被遮挡后没有图形补丁；源码继续推进并在遮挡期间闭合。
  await page.evaluate(()=>{
   window.chartMutations=0
   window.observer=new MutationObserver(records=>{window.chartMutations+=records.length})
   window.observer.observe(document.querySelector('figure svg'),{subtree:true,attributes:true,childList:true,characterData:true})
   window.update(1)
  })
  await page.waitForFunction(()=>document.querySelector('figure figcaption')?.textContent.includes('200'))
  await page.evaluate(()=>window.update(2,true))
  await page.locator('[data-source-state]').filter({hasText:'2:true'}).waitFor()
  await page.clock.runFor(100)
  // 键盘焦点保留在设置中，语言和尺寸变化不关闭设置。
  await page.locator('#language-preference').focus()
  await page.keyboard.press('Tab')
  assert.equal(await page.getByRole('dialog').evaluate(node=>node.contains(document.activeElement)),true)
  assert.equal(await page.locator('figure svg text').textContent(),'Version 0')
  assert.equal(await page.evaluate(()=>window.chartMutations),0)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.evaluate(()=>window.observer.disconnect())
  await page.keyboard.press('Escape')
  await page.getByRole('dialog').waitFor({state:'detached'})
  await page.locator('figure svg text').filter({hasText:'Version 2'}).waitFor()
  assert.equal(await page.getByRole('textbox',{name:'Draft',exact:true}).inputValue(),'保留草稿')
  await page.evaluate(()=>window.update(0))
  await page.locator('figure svg text').filter({hasText:'Version 0'}).waitFor()
 }
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Streaming SVG: settings shell stays usable; covered drawing pauses, final source resumes and draft survives language/theme/width matrix')
},{entry:'/__settings-chart-stream-entry.js',vite:{plugins:[{
 name:'settings-chart-stream-fixture',resolveId(id){if(id==='/__settings-chart-stream-entry.js')return id},load(id){if(id==='/__settings-chart-stream-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__settings-chart-stream',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__settings-chart-stream','<html><body><div id="root"></div><script type="module" src="/__settings-chart-stream-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
