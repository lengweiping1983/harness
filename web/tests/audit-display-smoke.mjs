import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {useHotkeys} from '/src/hooks/useHotkeys.ts'
import {useAppStore} from '/src/store/app.ts'
import {RunHistoryCard} from '/src/components/inspector/RunHistoryCard.tsx'
import {PermissionMenu} from '/src/components/chat/PermissionMenu.tsx'
import {usePermissionStore} from '/src/store/permissions.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.configure=(locale,theme)=>{setLocale(locale);document.documentElement.className=theme}
window.state=()=>useAppStore.getState().commandOpen
window.overlay=(open)=>useAppStore.setState({settingsOpen:open,commandOpen:false})
window.permissions=()=>usePermissionStore.setState({policy:'read_only',loading:false})
function Fixture(){useHotkeys();return React.createElement(React.Fragment,null,
React.createElement('button',{id:'focus'},'Focus'),React.createElement('input',{'aria-label':'Draft'}),
React.createElement(PermissionMenu,{connection:'closed'}),
React.createElement(RunHistoryCard,{record:{id:'fixture',status:'completed',created_at:1700000000,text:'fixture task',started_at:null,finished_at:null},sessionId:'fixture',busy:false,onContinue:()=>{},onOriginal:()=>{}}))}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
const html='<html><body><div id="root"></div><script type="module" src="/__display-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 for(const [locale,theme,width] of [['zh-CN','light',390],['en','dark',1440]]){
  const strings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url)))
  const page=await browser.newPage({viewport:{width,height:900}})
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.goto(server.resolvedUrls.local[0]+'__display')
  await page.locator('#focus').waitFor()
  await page.evaluate(({locale,theme})=>{window.configure(locale,theme);window.permissions()},{locale,theme})
  await page.getByRole('button',{name:strings['ui.components.chat.PermissionMenu.e7a50098114c'].replace('{p0}',locale==='en'?'Read-only':'只读'),exact:true}).waitFor()
  const card=page.locator('[data-run-id="fixture"]')
  assert.equal(await card.locator('summary span.text-faint').textContent(),strings['ui.components.inspector.RunHistoryCard.7450cc5b16e2'])
  await card.locator(':scope > details > summary').click()
  const expected=await page.evaluate(locale=>new Date(1700000000*1000).toLocaleString(locale,{hour12:false}),locale)
  await card.getByText(expected,{exact:true}).waitFor()
  await page.getByRole('textbox',{name:'Draft'}).fill('/')
  assert.equal(await page.evaluate(()=>window.state()),false)
  await page.locator('#focus').focus()
  await page.keyboard.press('/')
  assert.equal(await page.evaluate(()=>window.state()),true)
  await page.evaluate(()=>window.overlay(true))
  await page.locator('#focus').focus();await page.keyboard.press('/')
  assert.equal(await page.evaluate(()=>window.state()),false)
  assert.deepEqual(errors,[])
  await page.close()
 }
 console.log('Audit display: localized dates, unknown duration, read-only label, slash opens search and respects input/overlay passed')
},{entry:'/__display-entry.js',vite:{plugins:[{name:'display-fixture',resolveId(id){if(id==='/__display-entry.js')return id},load(id){if(id==='/__display-entry.js')return entry},configureServer(server){server.middlewares.use('/__display',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__display',html))}catch(e){next(e)}})}}]}})
