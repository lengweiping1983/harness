import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {SettingsDialog} from '/src/components/overlays/SettingsDialog.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('zh-CN')
useAppStore.getState().setSettingsOpen(true)
createRoot(document.getElementById('root')).render(React.createElement(SettingsDialog))
`
const entryPath = '/__settings-scroll-entry.js'

await browserScenario(async ({server, browser}) => {
  const page = await browser.newPage({viewport: {width: 1280, height: 900}})
  await page.goto(server.resolvedUrls.local[0] + '__settings-scroll')
  const content = page.locator('#settings-content')
  await content.getByRole('button', {name: '浅色', exact: true}).waitFor()
  const shortcuts = page.getByRole('button', {name: '快捷键', exact: true})
  const general = page.getByRole('button', {name: '通用', exact: true})
  await shortcuts.click()
  await content.evaluate(element => {element.scrollTop = element.scrollHeight})
  assert.ok(await content.evaluate(element => element.scrollTop > 0))
  await general.click()
  assert.deepEqual(await content.evaluate(element => ({page: element.dataset.page, top: element.scrollTop})), {page: 'general', top: 0})
  await shortcuts.click()
  await content.evaluate(element => {element.scrollTop = element.scrollHeight})
  await general.click()
  assert.equal(await content.evaluate(element => element.scrollTop), 0)
  console.log('Settings page navigation resets content scroll position')
  await page.close()
}, {entry: entryPath, vite: {plugins: [{
  name: 'settings-scroll-fixture',
  resolveId(id) {if (id === entryPath) return id},
  load(id) {if (id === entryPath) return entry},
  configureServer(server) {
    server.middlewares.use('/__settings-scroll', async (_req, res, next) => {
      try {
        res.setHeader('Content-Type', 'text/html')
        res.end(await server.transformIndexHtml('/__settings-scroll', '<html><body><div id="root"></div><script type="module" src="/__settings-scroll-entry.js"></script></body></html>'))
      } catch (error) {next(error)}
    })
  },
}]}})
