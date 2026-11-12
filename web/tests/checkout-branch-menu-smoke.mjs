import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ConversationCheckoutFields} from '/src/components/chat/ConversationCheckout.tsx'
import {Overlay} from '/src/components/ui/primitives.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
const long='feature/原始分支名/'+ 'long-name-'.repeat(12)
const options={cwd:'/fixture',is_git:true,root:'/fixture',branch:'main',head:'abc',dirty:false,local_branches:['main',long,...Array.from({length:40},(_,i)=>'feature/'+i)],refs:['main',long,'origin/remote']}
window.copy=t;window.locale=setLocale;window.long=long;window.changes=[];window.refreshes=0;window.dialogCloses=0
function Fixture(){
 const [value,setValue]=useState({cwd:'/fixture',mode:'local',ref:'main'}),[disabled,setDisabled]=useState(false),[compact,setCompact]=useState(true),[dialog,setDialog]=useState(false)
 window.disable=setDisabled;window.compact=setCompact;window.project=cwd=>setValue(current=>({...current,cwd}));window.dialogWrap=setDialog
 const fields=React.createElement('main',{className:'p-4'},React.createElement('div',{className:'refresh-action-container flex min-w-0 flex-wrap items-center gap-2'},
  React.createElement(ConversationCheckoutFields,{options,value,compact,disabled,onChange:next=>{window.changes.push(next);setValue(next)},onRefresh:()=>window.refreshes++})),
  React.createElement('input',{'aria-label':'Original draft',className:'ml-auto block',defaultValue:'Original 草稿'}))
 return dialog?React.createElement(Overlay,{onClose:()=>{window.dialogCloses++;setDialog(false)},className:'max-w-lg'},fields):fields
}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`

await browserScenario(async ({server, browser}) => {
  const page = await browser.newPage(), errors = [], writes = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', route => {writes.push(route.request().method()); return route.fulfill({status:500})})
  await page.goto(server.resolvedUrls.local[0] + '__checkout-branch')
  const anchor = page.locator('button[aria-haspopup="menu"]'), menu = page.getByRole('menu'), items = page.getByRole('menuitemradio')
  for (const language of ['en', 'zh-CN', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) for (const size of [10, 16, 22]) {
    await page.setViewportSize({width, height:800})
    await page.evaluate(({language,theme,size}) => {window.locale(language); document.documentElement.className=theme; document.documentElement.style.fontSize=size+'px'}, {language,theme,size})
    await anchor.click()
    await menu.waitFor()
    assert.equal(await items.filter({hasText:'origin/remote'}).count(), 0)
    assert.equal(await items.filter({hasText:/^main$/}).count(), 1)
    assert.equal(await items.first().getAttribute('aria-checked'), 'true')
    const bounds = await menu.boundingBox()
    assert.ok(bounds.x >= 12 && bounds.x + bounds.width <= width - 12)
    assert.ok(bounds.y >= 12 && bounds.y + bounds.height <= 788)
    const long = await page.evaluate(() => window.long)
    const row = items.filter({hasText:long})
    assert.equal(await row.textContent(), long)
    assert.ok(await row.evaluate(node => node.scrollWidth <= node.clientWidth))
    for (const key of ['Enter',' ','Escape','ArrowDown']) for (const mode of ['composing','229']) {
      const before = await page.evaluate(() => window.changes.length)
      await items.first().evaluate((node,{key,mode}) => node.dispatchEvent(new KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,isComposing:mode==='composing',keyCode:mode==='229'?229:0})), {key,mode})
      assert.equal(await anchor.getAttribute('aria-expanded'), 'true')
      assert.equal(await page.evaluate(() => window.changes.length), before)
    }
    await items.first().focus(); await page.keyboard.press('End')
    assert.equal(await items.last().evaluate(node => document.activeElement===node), true)
    await page.keyboard.press('Home'); await page.keyboard.press('ArrowDown')
    assert.equal(await row.evaluate(node => document.activeElement===node), true)
    await page.keyboard.press('Escape')
    assert.equal(await anchor.evaluate(node => document.activeElement===node), true)
    await anchor.press('ArrowDown'); await items.first().press('Tab')
    await menu.waitFor({state:'hidden'})
    assert.equal(await page.getByRole('switch').evaluate(node => document.activeElement===node), true)
  }
  await anchor.click(); await items.nth(1).click()
  assert.equal(await anchor.getAttribute('title'), await page.evaluate(() => window.long))
  assert.equal(await page.evaluate(() => window.changes.length), 1)
  assert.equal(await anchor.evaluate(node => document.activeElement===node), true)
  await page.getByRole('switch').click(); await anchor.click()
  await items.filter({hasText:'origin/remote'}).click()
  assert.equal(await anchor.getAttribute('title'), 'origin/remote')
  await page.getByRole('switch').click()
  assert.equal(await anchor.getAttribute('title'), 'main')
  await anchor.click(); await page.getByRole('switch').click()
  await menu.waitFor({state:'hidden'})
  await anchor.click(); await page.evaluate(() => window.project('/other-project'))
  await menu.waitFor({state:'hidden'})
  await anchor.click(); await page.evaluate(() => window.disable(true))
  await menu.waitFor({state:'hidden'})
  assert.equal(await anchor.isDisabled(), true)
  await page.evaluate(() => {window.disable(false); window.compact(false)})
  const draft = page.getByRole('textbox', {name:'Original draft',exact:true})
  await anchor.click(); await menu.waitFor()
  assert.equal(await draft.evaluate(node => {
    const box=node.getBoundingClientRect()
    return document.elementFromPoint(box.x+box.width/2,box.y+box.height/2)===node
  }), true, 'Outside-click target must be outside the branch menu')
  await draft.click()
  await menu.waitFor({state:'hidden'})
  assert.equal(await draft.evaluate(node => document.activeElement===node), true)
  assert.equal(await draft.inputValue(), 'Original 草稿')
  await anchor.click(); await draft.focus()
  await menu.waitFor({state:'hidden'})
  assert.equal(await draft.evaluate(node => document.activeElement===node), true)
  await page.evaluate(() => window.dialogWrap(true))
  await page.getByRole('dialog').waitFor()
  await anchor.click(); await menu.waitFor()
  assert.equal(await menu.evaluate(node => {
    const box=node.getBoundingClientRect()
    return node.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2))
  }), true)
  await page.keyboard.press('Escape')
  await menu.waitFor({state:'hidden'})
  assert.equal(await page.getByRole('dialog').count(), 1)
  assert.equal(await page.evaluate(() => window.dialogCloses), 0)
  await anchor.click(); await items.first().press('Tab')
  await menu.waitFor({state:'hidden'})
  assert.equal(await page.getByRole('switch').evaluate(node => document.activeElement===node), true)
  assert.equal(await page.getByRole('dialog').count(), 1)
  assert.deepEqual(writes, [])
  assert.deepEqual(errors, [])
}, {entry:'/__checkout-branch-entry.js', vite:{plugins:[{name:'checkout-branch-fixture',
  resolveId(id) {if (id==='/__checkout-branch-entry.js') return id},
  load(id) {if (id==='/__checkout-branch-entry.js') return entry},
  configureServer(server) {server.middlewares.use('/__checkout-branch', async (_request,response,next) => {
    try {response.setHeader('Content-Type','text/html'); response.end(await server.transformIndexHtml('/__checkout-branch','<html><body><div id="root"></div><script type="module" src="/__checkout-branch-entry.js"></script></body></html>'))}
    catch (error) {next(error)}
  })},
}]}})
