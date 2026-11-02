import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Mermaid} from '/src/components/markdown/Mermaid.tsx'
import {AutomationsPage} from '/src/components/overlays/AutomationsPage.tsx'
import {McpServicePanel} from '/src/components/overlays/McpServicePanel.tsx'
import {LiveChart} from '/src/components/markdown/LiveChart.tsx'
import {CodeBlock} from '/src/components/markdown/CodeBlock.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
const root = createRoot(document.getElementById('root'))
window.locale = setLocale
window.copy = t
window.chartSource = JSON.stringify({mark:'bar',encoding:{x:{field:'x'}},data:{values:[{x:1}]}})
window.charts = () => root.render(React.createElement('div', null,
  React.createElement(Mermaid, {chart:'graph TD; A[开始]-->B[完成]'}),
  React.createElement(Mermaid, {chart:'graph TD; C[第二张]-->D[完成]'}),
  React.createElement(LiveChart, {lang:'vega-lite', code:window.chartSource,closed:true}),
  React.createElement(CodeBlock, {code:'const value = "hello"; // comment'}),
  React.createElement(CodeBlock, {language:'javascript',code:'const value = "hello"; // comment'})))
window.automations = () => root.render(React.createElement(AutomationsPage))
window.clear = () => root.render(null)
window.mcp = () => root.render(React.createElement(McpServicePanel))
window.ready = true
`
const html = '<!doctype html><html><body><div id="root"></div><script type="module" src="/__issues-entry.js"></script></body></html>'

await browserScenario(async ({server, browser}) => {
  const page = await browser.newPage({viewport:{width:390,height:900}})
  const errors = []
  page.on('pageerror', error => {errors.push(error.message); console.error(error.message)})
  await page.goto(server.resolvedUrls.local[0] + '__issues')
  await page.waitForFunction(() => window.ready)
  await page.evaluate(async () => {await window.locale('zh-CN'); window.charts()})
  await page.waitForFunction(() => document.querySelectorAll('.mermaid-host svg').length === 2)
  assert.ok(await page.getByText('纯文本', {exact:true}).count())
  const chart = page.locator('figure').filter({has:page.locator('iframe')})
  const frame = chart.frameLocator('iframe')
  await frame.locator('#chart[data-chart-ready="true"]').waitFor()
  await chart.locator('[aria-busy="false"] > iframe').waitFor()
  assert.ok(await frame.locator('.mark-rect path').count(), 'Vega-Lite must render actual bars')
  await frame.locator('#chart').evaluate(node => {window.originalChart = node})
  await page.locator('.hljs-keyword').waitFor()
  const keywordLight = await page.locator('.hljs-keyword').first().evaluate(element => getComputedStyle(element).color)
  const light = await page.locator('.mermaid-host .node rect').first().evaluate(element => getComputedStyle(element).fill)
  await mkdir('../output/issues-ui', {recursive:true})
  await page.screenshot({path:'../output/issues-ui/zh-light-narrow.png',fullPage:true})
  await page.evaluate(() => document.documentElement.classList.add('dark'))
  await page.waitForFunction(fill => {
    const rects = [...document.querySelectorAll('.mermaid-host .node rect')]
    return rects.length >= 4 && rects.every(element => getComputedStyle(element).fill !== fill)
  }, light)
  await page.setViewportSize({width:1440,height:900})
  await page.evaluate(() => window.locale('en'))
  await page.getByText('Plain text', {exact:true}).waitFor()
  for (const language of ['en','zh-CN','en']) {
    await page.evaluate(language => window.locale(language), language)
    const labels = await page.evaluate(() => ({code:window.copy('ui.widget.viewCode'),rendering:window.copy('ui.widget.viewWidget')}))
    await chart.getByRole('button', {name:labels.code,exact:true}).click()
    assert.equal(await chart.locator('pre').textContent(), await page.evaluate(() => window.chartSource))
    await chart.getByRole('button', {name:labels.rendering,exact:true}).click()
    await chart.locator('[aria-busy="false"] > iframe').waitFor()
    assert.equal(await frame.locator('#chart').evaluate(node => node === window.originalChart), true)
    assert.ok(await frame.locator('.mark-rect path').count())
  }
  assert.notEqual(await page.locator('.hljs-keyword').first().evaluate(element => getComputedStyle(element).color), keywordLight)
  await page.screenshot({path:'../output/issues-ui/en-dark-wide.png',fullPage:true})
  let calls = 0, release
  const firstResponse = new Promise(resolve => {release = resolve})
  await page.route('**/api/v1/automations', async route => {
    calls++
    if (calls === 1) await firstResponse
    if (calls === 2) {await route.fulfill({status:500,json:{message:'fixture load failure'}}); return}
    await route.fulfill({json:{automations:[],runs:[],scheduler:{running:false,max_concurrent:1,owner:false}}})
  })
  await page.clock.install()
  await page.evaluate(() => {
    window.__hidden = false
    Object.defineProperty(document, 'hidden', {get: () => window.__hidden, configurable:true})
    window.automations()
  })
  await page.getByRole('status').waitFor()
  release()
  await page.getByRole('tabpanel').waitFor()
  assert.equal(calls, 1)
  await page.evaluate(() => {window.__hidden = true; document.dispatchEvent(new Event('visibilitychange'))})
  await page.clock.fastForward(12000)
  assert.equal(calls, 1, 'Hidden page must pause polling')
  await page.evaluate(() => {window.__hidden = false; document.dispatchEvent(new Event('visibilitychange'))})
  await page.getByRole('alert').waitFor()
  assert.equal(calls, 2, 'Visible page must refresh immediately')
  const recovered = page.waitForResponse(response => response.url().endsWith('/api/v1/automations') && response.status() === 200)
  await page.clock.fastForward(4000)
  await recovered
  await page.getByRole('alert').waitFor({state:'detached'})
  await page.evaluate(() => window.clear())
  await page.clock.fastForward(12000)
  assert.equal(calls, 3, 'Unmounted page must stop polling')
  let mcpCalls = 0, releaseOldStatus
  const oldStatus = new Promise(resolve => {releaseOldStatus = resolve})
  await page.route('**/api/v1/mcp-service', async route => {
    mcpCalls++
    if (mcpCalls === 3) await oldStatus
    await route.fulfill({json:{status:'stopped',local_url:'',public_url:'',project_root:'',workspaces:[],error:'',error_copy:null}})
  })
  const firstMcp = page.waitForResponse(response => response.url().endsWith('/api/v1/mcp-service'))
  await page.evaluate(() => window.mcp())
  await firstMcp
  const start = page.getByRole('button', {name:'Start MCP',exact:true})
  await start.waitFor()
  await page.evaluate(() => {window.__hidden = true; document.dispatchEvent(new Event('visibilitychange'))})
  await page.clock.fastForward(10000)
  assert.equal(mcpCalls, 1, 'Hidden MCP service panel must pause polling')
  const nextMcp = page.waitForResponse(response => response.url().endsWith('/api/v1/mcp-service'))
  await page.evaluate(() => {window.__hidden = false; document.dispatchEvent(new Event('visibilitychange'))})
  await nextMcp
  assert.equal(mcpCalls, 2, 'Visible MCP service panel must refresh')
  await page.route('**/api/v1/mcp-service/start', route => route.fulfill({json:{status:'running',local_url:'http://127.0.0.1:1/mcp',public_url:'https://example.test/mcp',project_root:'',workspaces:[],error:'',error_copy:null}}))
  const staleRequest = page.waitForRequest(request => request.url().endsWith('/api/v1/mcp-service'))
  await page.clock.fastForward(2000)
  await staleRequest
  await start.click()
  await page.getByRole('button', {name:'Stop MCP',exact:true}).waitFor()
  const staleResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/mcp-service'))
  releaseOldStatus()
  await staleResponse
  await page.evaluate(async () => { await Promise.resolve(); await Promise.resolve() })
  assert.equal(await page.getByRole('button', {name:'Stop MCP',exact:true}).count(), 1, 'Old status must not overwrite a successful start')
  await page.evaluate(() => window.clear())
  await page.clock.fastForward(10000)
  assert.equal(mcpCalls, 3, 'Unmounted MCP service panel must stop polling')
  assert.deepEqual(errors, [])
  await page.close()
  console.log('Issue review: live Mermaid theme changes, two concurrent diagrams, bilingual chart labels, responsive layouts, hidden/unmounted automation and MCP polling, and stale MCP status isolation passed')
}, {entry:'/__issues-entry.js', vite:{plugins:[{name:'issue-review-fixture',resolveId(id){if(id==='/__issues-entry.js') return id},load(id){if(id==='/__issues-entry.js') return entry},configureServer(server){server.middlewares.use('/__issues', async (_req,res,next) => {try {res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__issues',html))} catch(error){next(error)}})}}]}})
