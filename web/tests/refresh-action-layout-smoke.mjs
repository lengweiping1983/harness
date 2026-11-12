import assert from 'node:assert/strict'
import { browserScenario } from './browser-scenario.mjs'

const entry = `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { RefreshButton } from '/src/components/ui/primitives.tsx'
import { setLocale, t, useLocale } from '/src/i18n/index.ts'
import '/src/styles.css'
window.changeLanguage = setLocale
window.clicks = 0
function Demo() {
  useLocale()
  return React.createElement('section', { className: 'refresh-action-container', id: 'container', style: { width: '600px' } },
    React.createElement(RefreshButton, {
      id: 'default-refresh',
      size: 'xs',
      label: t('ui.components.inspector.ContextAuditPanel.920b5628b33a'),
      onClick: () => { window.clicks++ },
    }),
    React.createElement(RefreshButton, { id: 'ghost-reference', size: 'xs', variant: 'ghost', label: 'Reference refresh' }))
}
createRoot(document.getElementById('root')).render(React.createElement(Demo))
`

await browserScenario(async ({ server, browser }) => {
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0] + '__refresh-action-layout')
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.evaluate(language => window.changeLanguage(language), language)
    const label = language === 'en' ? 'Refresh context audit' : '刷新上下文审计'
    const visible = language === 'en' ? 'Refresh' : '刷新'
    const button = page.getByRole('button', { name: label, exact: true })
    await button.waitFor()
    await page.mouse.move(0, 0)
    await page.waitForTimeout(200)
    const appearance = await page.locator('#default-refresh, #ghost-reference').evaluateAll(elements => elements.map(element => {
      const style = getComputedStyle(element)
      return { background: style.backgroundColor, border: style.borderTopWidth, color: style.color, height: style.height }
    }))
    assert.deepEqual(appearance[0], appearance[1], 'Default refresh style must match the shared borderless style')
    await page.locator('#container').evaluate(element => { element.style.width = '600px' })
    assert.notEqual(await button.locator('.refresh-action-label').evaluate(element => getComputedStyle(element).display), 'none')
    assert.equal(await button.locator('.refresh-action-label').textContent(), visible)
    await page.locator('#container').evaluate(element => { element.style.width = '280px' })
    assert.equal(await button.locator('.refresh-action-label').evaluate(element => getComputedStyle(element).display), 'none')
    assert.equal(await button.locator('svg').count(), 1)
    await button.click()
  }
  assert.equal(await page.evaluate(() => window.clicks), 3)
  assert.deepEqual(errors, [])
  await page.close()
  console.log('Refresh action keeps its icon, full accessible name and click behavior across wide and narrow containers and language changes')
}, { entry: '/__refresh-action-layout-entry.js', vite: { plugins: [{
  name: 'refresh-action-layout',
  resolveId(id) { if (id === '/__refresh-action-layout-entry.js') return id },
  load(id) { if (id === '/__refresh-action-layout-entry.js') return entry },
  configureServer(server) {
    server.middlewares.use('/__refresh-action-layout', async (_request, response, next) => {
      try {
        response.setHeader('Content-Type', 'text/html')
        response.end(await server.transformIndexHtml('/__refresh-action-layout', '<html><body><div id="root"></div><script type="module" src="/__refresh-action-layout-entry.js"></script></body></html>'))
      } catch (error) { next(error) }
    })
  },
}] } })
