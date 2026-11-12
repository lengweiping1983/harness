import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const source = '<video src="one.mp4"></video>middle<video><source src="two.mp4"></video>\n\n$x$ plus $y$ plus $z'
const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Markdown} from '/src/components/markdown/Markdown.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
const root = createRoot(document.getElementById('root'))
window.show = (text, language, theme) => {
  setLocale(language)
  document.documentElement.className = theme
  root.render(React.createElement(Markdown, {streaming:true}, text))
}
window.show(${JSON.stringify(source)}, 'en', 'light')
`
const html = '<!doctype html><html><body><div id="root"></div><script type="module" src="/__markdown-entry.js"></script></body></html>'
await browserScenario(async ({server, browser}) => {
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', route => route.fulfill({json:{}}))
  await page.goto(server.resolvedUrls.local[0] + '__markdown')
  for (const language of ['en', 'zh-CN']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    await page.setViewportSize({width, height:900})
    await page.evaluate(({source, language, theme}) => window.show(source, language, theme), {source, language, theme})
    await page.waitForFunction(() => document.querySelectorAll('annotation[encoding="application/x-tex"]').length === 3)
    assert.deepEqual(await page.locator('annotation[encoding="application/x-tex"]').allTextContents(), ['x', 'y', 'z'])
    await page.getByText('one.mp4', {exact:true}).waitFor()
    await page.getByText('two.mp4', {exact:true}).waitFor()
    assert.ok((await page.locator('.md').textContent()).includes('middle'))
    const block = '$$\nx + y\n$$\n\n`$VAR`\n\n```html\n<video src="example.mp4"></video>\n```'
    await page.evaluate(({block, language, theme}) => window.show(block, language, theme), {block, language, theme})
    await page.waitForFunction(() => document.querySelector('annotation[encoding="application/x-tex"]')?.textContent === 'x + y')
    assert.equal(await page.locator('annotation[encoding="application/x-tex"]').count(), 1)
    assert.ok((await page.locator('code').allTextContents()).some(text => text.includes('<video src="example.mp4"></video>')))
    assert.ok((await page.locator('code').allTextContents()).includes('$VAR'))
  }
  assert.deepEqual(errors, [])
  await page.close()
  console.log('Streaming Markdown: multiple media, complete and incomplete math, literal code, bilingual themes and widths passed')
}, {entry:'/__markdown-entry.js', vite:{plugins:[{
  name:'markdown-fixture',
  resolveId(id) {if (id === '/__markdown-entry.js') return id},
  load(id) {if (id === '/__markdown-entry.js') return entry},
  configureServer(server) {
    server.middlewares.use('/__markdown', async (_request, response, next) => {
      try {response.setHeader('Content-Type', 'text/html'); response.end(await server.transformIndexHtml('/__markdown', html))}
      catch (error) {next(error)}
    })
  },
}]}})
