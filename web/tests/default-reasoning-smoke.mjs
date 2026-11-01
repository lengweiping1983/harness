import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import path from 'node:path'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React,{useRef,useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ModelMenu} from '/src/components/chat/ModelPicker.tsx'
import {useAnchoredMenu} from '/src/hooks/useAnchoredMenu.ts'
import {useAppStore} from '/src/store/app.ts'
import {reasoningSelection} from '/src/lib/reasoningSelection.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
const models=[{id:'fixture',label:'GPT-6.1-Sol',provider:'chatgpt',visible:true,supports_reasoning:true,reasoning_efforts:['low','medium','high','xhigh','max','ultra']}]
useAppStore.setState({activeSessionId:null,defaultModel:'fixture',defaultReasoningEffort:null,defaultReasoningSave:null,models,sessions:[]})
window.locale=setLocale;window.copy=t;window.store=useAppStore
function Fixture(){
 const anchor=useRef(null),[open,setOpen]=useState(true)
 const currentModels=useAppStore(s=>s.models)
 window.openMenu=()=>setOpen(true)
 const effort=useAppStore(s=>reasoningSelection(s.sessions.find(row=>row.id===s.activeSessionId),s.defaultReasoningEffort).value)
 const style=useAnchoredMenu(anchor,open,'model')
 return React.createElement(React.Fragment,null,React.createElement('button',{ref:anchor,style:{position:'fixed',right:16,bottom:40},onClick:()=>setOpen(!open)},'Model'),open?React.createElement('div',{style},React.createElement(ModelMenu,{models:currentModels,current:'fixture',effort,onClose:()=>setOpen(false)})):null)
}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`

await browserScenario(async ({server, browser}) => {
  const page = await browser.newPage(), errors = [], writes = []
  let actual = null, malformed = false, reads = 0
  const settings = () => ({reasoning_effort: actual, deepseek_web_enabled: false, deepseek_web_api_key_configured: false, brave_web_enabled: false, brave_web_api_key_configured: false})
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url())
    if (url.pathname.endsWith('/connection')) return route.fulfill({json: {models: [], current: null, cycle: 1}})
    if (url.pathname.endsWith('/ui-settings')) {
      if (request.method() === 'PATCH') {
        const body = request.postDataJSON(); writes.push(body)
        if (malformed) return route.fulfill({status: 200, contentType: 'application/json', body: 'not json'})
        actual = body.reasoning_effort
        return route.fulfill({json: {settings: settings(), rejected: []}})
      }
      reads++
      return route.fulfill({json: {settings: settings()}})
    }
    errors.push('Unexpected request: ' + request.method() + ' ' + url.pathname)
    return route.fulfill({status: 500, json: {error: 'Unexpected fixture request'}})
  })
  await page.goto(server.resolvedUrls.local[0] + '__default-reasoning')
  const menu = page.getByRole('listbox')
  const waitSaved = effort => page.waitForFunction(effort => {
    const state = window.store.getState()
    return state.defaultReasoningSave?.pending === false && state.defaultReasoningSave.error === null && state.defaultReasoningEffort === effort
  }, effort)
  for (const language of ['en', 'zh-CN', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) for (const font of [10, 16, 22]) {
    await page.setViewportSize({width, height: 1000})
    const previous = writes.length
    await page.evaluate(({language, theme, font}) => {
      window.locale(language); document.documentElement.className = theme
      document.documentElement.dataset.theme = theme; document.documentElement.style.fontSize = font + 'px'
    }, {language, theme, font})
    const label = await page.evaluate(() => window.copy('ui.components.chat.ModelPicker.8cf0105d35ee'))
    const group = menu.getByRole('group', {name: label, exact: true})
    await page.waitForFunction(label => {
      const group = [...document.querySelectorAll('[role="group"]')].find(node => node.getAttribute('aria-label') === label)
      return group && [...group.querySelectorAll('button')].every(button => {
        const box = button.getBoundingClientRect()
        return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2) === button
      })
    }, label, {timeout: 5000})
    assert.equal(await group.getByRole('button').count(), 7)
    const geometry = await group.evaluate(node => {
      const bounds = node.getBoundingClientRect()
      return [...node.querySelectorAll('button')].map(button => {
        const box = button.getBoundingClientRect()
        return {inside: box.left >= bounds.left && box.right <= bounds.right, hit: document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2) === button, disabled: button.disabled}
      })
    })
    assert.ok(geometry.every(item => item.inside && item.hit && !item.disabled), JSON.stringify({language, theme, width, font, geometry}))
    const line = await menu.getByText(label, {exact: true}).evaluate(node => {
      const range = document.createRange(); range.selectNodeContents(node); return range.getClientRects().length
    })
    assert.equal(line, 1, 'Reasoning label must stay on one line')
    assert.equal(writes.length, previous, 'Presentation changes must not save again')
    await group.getByRole('button', {name: 'high', exact: true}).focus()
    await page.keyboard.press('Enter')
    await waitSaved('high')
    await group.getByRole('button', {name: 'high', exact: true}).getAttribute('aria-pressed').then(value => assert.equal(value, 'true'))
    assert.equal(await menu.count(), 1, 'Choosing an effort must not commit a model or close the menu')
    await page.keyboard.press('Escape')
    await menu.waitFor({state: 'detached'})
    await page.evaluate(() => window.openMenu())
    await menu.waitFor()
    assert.equal(await menu.getByRole('button', {name: 'high', exact: true}).getAttribute('aria-pressed'), 'true')
    if (language === 'zh-CN' && theme === 'light' && width === 390 && font === 16) {
      const folder = path.resolve('..', 'output', 'playwright')
      await mkdir(folder, {recursive: true})
      await page.screenshot({path: path.join(folder, 'reasoning-before-session.png')})
    }
    await menu.getByRole('button', {name: await page.evaluate(() => window.copy('ui.components.chat.ModelPicker.9a36b187cea4')), exact: true}).click()
    await waitSaved(null)
    assert.equal(writes.length, previous + 2)
  }
  // 损坏成功回执只读回一次；同一菜单中的反馈随语言变化，不重放保存。
  malformed = true
  await menu.getByRole('button', {name: 'high', exact: true}).click()
  await page.waitForFunction(() => window.store.getState().defaultReasoningSave?.error && !window.store.getState().defaultReasoningSave.pending)
  const count = writes.length
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.evaluate(language => window.locale(language), language)
    const strings = await page.evaluate(() => ({label: window.copy('ui.components.chat.ModelPicker.49fd236d4f7a'), text: window.copy('ui.store.app.340d62606aa1', {p0: window.copy('ui.http.invalidResponse')})}))
    await menu.getByRole('alert', {name: strings.label}).getByText(strings.text, {exact: true}).waitFor()
    assert.equal(writes.length, count); assert.equal(reads, 1)
  }
  malformed = false
  await page.evaluate(() => window.store.setState({models: window.store.getState().models.map(model => ({...model, reasoning_efforts: []}))}))
  await menu.getByRole('group').waitFor({state: 'detached'})
  await menu.getByRole('button', {name: await page.evaluate(() => window.copy('ui.models.resetReasoning')), exact: true}).click()
  await waitSaved(null)
  await page.evaluate(() => window.store.setState({models: window.store.getState().models.map(model => ({...model, reasoning_efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']}))}))
  await menu.getByRole('button', {name: 'ultra', exact: true}).click()
  await waitSaved('ultra')
  assert.deepEqual(writes.at(-1), {reasoning_effort: 'ultra'})
  assert.equal(await menu.getByRole('alert').count(), 0)
  assert.equal(await page.evaluate(() => window.store.getState().activeSessionId), null)
  assert.deepEqual(errors, [])
  await page.close()
  console.log('Default reasoning: actual keyboard selection, reopening, model default, layout matrix and bilingual save recovery verified')
}, {entry: '/__default-reasoning-entry.js', vite: {plugins: [{
  name: 'default-reasoning-fixture',
  resolveId(id) {if (id === '/__default-reasoning-entry.js') return id},
  load(id) {if (id === '/__default-reasoning-entry.js') return entry},
  configureServer(server) {server.middlewares.use('/__default-reasoning', async (_req, res, next) => {
    try {res.setHeader('Content-Type', 'text/html'); res.end(await server.transformIndexHtml('/__default-reasoning', '<html><body><div id="root"></div><script type="module" src="/__default-reasoning-entry.js"></script></body></html>'))}
    catch (error) {next(error)}
  })},
}]}})
