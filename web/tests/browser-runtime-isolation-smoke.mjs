import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

// 同路径、同插件名连续替换正文；真实组件及存储不能继承上一场景的状态。
for (const value of ['first 原文', 'replacement 原文', 'first 原文']) {
  const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {CopyButton} from '/src/components/ui/primitives.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.fixtureValue=${JSON.stringify(value)}
window.locale=setLocale
createRoot(document.getElementById('root')).render(React.createElement(CopyButton,{text:window.fixtureValue,label:window.fixtureValue}))
`
  await browserScenario(async ({server, browser}) => {
    const page = await browser.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(server.resolvedUrls.local[0] + '__runtime-isolation')
    await page.getByRole('button', {name: value, exact: true}).waitFor()
    assert.equal(await page.evaluate(() => window.fixtureValue), value)
    assert.equal(await page.evaluate(() => localStorage.getItem('fixture-ownership')), null)
    assert.equal(await page.evaluate(() => sessionStorage.getItem('fixture-ownership')), null)
    assert.equal((await page.context().cookies()).length, 0)
    await page.evaluate(() => {
      localStorage.setItem('fixture-ownership', 'original')
      sessionStorage.setItem('fixture-ownership', 'original')
      document.cookie = 'fixture=original; path=/'
    })
    for (const language of ['en', 'zh-CN', 'en']) {
      await page.evaluate(language => window.locale(language), language)
      await page.getByRole('button', {name: value, exact: true}).waitFor()
    }
    assert.deepEqual(errors, [])
    await page.close()
  }, {entry: '/__runtime-isolation-entry.js', vite: {plugins: [{
    name: 'runtime-isolation-fixture',
    resolveId(id) {if (id === '/__runtime-isolation-entry.js') return id},
    load(id) {if (id === '/__runtime-isolation-entry.js') return entry},
    configureServer(server) {
      server.middlewares.use('/__runtime-isolation', async (_request, response, next) => {
        try {
          response.setHeader('Content-Type', 'text/html')
          response.end(await server.transformIndexHtml('/__runtime-isolation', '<html><body><div id="root"></div><script type="module" src="/__runtime-isolation-entry.js"></script></body></html>'))
        } catch (error) {next(error)}
      })
    },
  }]}})
}
console.log('Shared runtime: same-path fixture replacement, original product component and fresh cookies/local/session storage passed')
