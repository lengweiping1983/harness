import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entryPath = '/__settings-search-entry.js'
const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {SettingsDialog} from '/src/components/overlays/SettingsDialog.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('zh-CN');window.locale=setLocale;window.copy=t
useAppStore.getState().setSettingsOpen(true)
createRoot(document.getElementById('root')).render(React.createElement(SettingsDialog))
`

await browserScenario(async ({server, browser}) => {
  const page = await browser.newPage({viewport: {width: 1280, height: 900}})
  const writes=[]
  await page.route('**/api/v1/settings',route=>{
    if(route.request().method()!=='GET')writes.push(route.request().method())
    return route.fulfill({json:{config:{permission_policy:'request_approval',max_steps:10,token_budget:null,network_timeout:60,context_auto_manage:true,context_working_budget:7200,tool_result_max_chars:12000,tool_schema_mode:'auto',summary_max_tokens:8192,output_reserve:1000,context_window:12000},effective:{model:'Redundant model',context_window:12000,max_tokens:2000,sources:['manual']}}})
  })
  await page.goto(server.resolvedUrls.local[0] + '__settings-search')
  const search = page.locator('input[type="search"]')
  await search.waitFor()
  await search.fill('快捷键')
  await page.getByRole('button', {name: '快捷键', exact: true}).waitFor()
  assert.deepEqual(await page.locator('nav button[data-page]:visible').evaluateAll(nodes => nodes.map(node => node.dataset.page)), ['shortcuts', 'voice'])
  await search.press('Escape')
  assert.equal(await search.evaluate(element => element.value), '')
  assert.ok(await page.locator('nav button[data-page]:visible').count() > 1)
  const copy=key=>page.evaluate(key=>window.copy(key),key)
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
    await page.setViewportSize({width,height:900})
    await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
    await search.focus()
    const assertFocus=async input=>{
      const style=await input.evaluate(node=>({inner:getComputedStyle(node).outlineStyle,outer:getComputedStyle(node.parentElement).outlineStyle,width:getComputedStyle(node.parentElement).outlineWidth,offset:getComputedStyle(node.parentElement).outlineOffset}))
      assert.deepEqual(style,{inner:'none',outer:'solid',width:'2px',offset:'-2px'})
    }
    await assertFocus(search)
    await page.locator('button[data-page="shortcuts"]').click()
    const shortcuts=page.getByRole('textbox',{name:await copy('ui.components.overlays.SettingsDialog.49504fdfc1b5'),exact:true})
    await shortcuts.focus();await assertFocus(shortcuts)
    await shortcuts.fill('NoSuchShortcut');await page.getByText(await copy('ui.components.overlays.SettingsDialog.078e8e583865'),{exact:true}).waitFor()
    await shortcuts.fill('')
    await page.locator('button[data-page="permissions"]').click()
    await page.getByRole('combobox').waitFor()
    assert.equal(await page.locator('#settings-content dl').count(),0)
    assert.equal(await page.getByText('Redundant model',{exact:true}).count(),0)
    assert.equal(await page.locator('[data-settings-save-hint]').count(),0)
    assert.equal(await page.getByText(/设置作用域：|Settings scope:/).count(),0)
    assert.equal(await page.locator('#settings-content').evaluate(node=>node.scrollWidth>node.clientWidth),false)
  }
  assert.deepEqual(writes,[])
  console.log('Settings search, Escape, single outer focus, removed hints and permission summary across languages, themes and widths')
  await page.close()
}, {entry: entryPath, vite: {plugins: [{
  name: 'settings-search-fixture',
  resolveId(id) {if (id === entryPath) return id},
  load(id) {if (id === entryPath) return entry},
  configureServer(server) {
    server.middlewares.use('/__settings-search', async (_req, res, next) => {
      try {
        res.setHeader('Content-Type', 'text/html')
        res.end(await server.transformIndexHtml('/__settings-search', '<html><body><div id="root"></div><script type="module" src="/__settings-search-entry.js"></script></body></html>'))
      } catch (error) {next(error)}
    })
  },
}]}})
