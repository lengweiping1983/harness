import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ConnectorsPage} from '/src/components/overlays/EscPanel.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
const root = createRoot(document.getElementById('root'))
window.mount = () => root.render(React.createElement(ConnectorsPage))
window.clear = () => root.render(null)
setLocale('en')
window.ready = true
`
const html = '<!doctype html><html><body><div id="root"></div><script type="module" src="/__connectors-entry.js"></script></body></html>'
const oauth = {supported:true,state:'pending',mode:'code'}
let connector = {name:'fixture-connector',transport:'http',url:'https://example.test/mcp',enabled:true,bound:false,connected:false,tool_count:0,oauth}

await browserScenario(async ({server,browser}) => {
  const page = await browser.newPage({viewport:{width:390,height:900}})
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  let statusCalls = 0, releaseFirst
  const firstGate = new Promise(resolve => {releaseFirst = resolve})
  await page.route('**/api/v1/plugins/connectors', route => route.fulfill({json:{enabled:true,connectors:[connector],environment:[]}}))
  await page.route('**/api/v1/plugins/connectors/presets', route => route.fulfill({json:{presets:[]}}))
  await page.route('**/api/v1/plugins/connectors/fixture-connector/oauth/status', async route => {
    const index = ++statusCalls
    if (index === 1) await firstGate
    await route.fulfill({json:index === 1 ? oauth : {supported:true,state:'authorized'}})
  })
  await page.addInitScript(() => {
    window.__abortedOAuth = 0
    const fetch = window.fetch
    window.fetch = (input, options) => {
      if (String(input).endsWith('/oauth/status')) options?.signal?.addEventListener('abort', () => window.__abortedOAuth++)
      return fetch(input, options)
    }
  })
  await page.goto(server.resolvedUrls.local[0] + '__connectors')
  await page.waitForFunction(() => window.ready)
  await page.clock.install()
  await page.evaluate(() => {
    window.__hidden = false
    Object.defineProperty(document,'hidden',{configurable:true,get:() => window.__hidden})
    window.mount()
  })
  const statusResponse = () => page.waitForResponse(response => response.url().endsWith('/oauth/status'))
  await page.getByText('fixture-connector',{exact:false}).first().waitFor().catch(async error => {console.error(await page.locator('body').innerText(), errors);throw error})
  await page.waitForFunction(() => document.querySelectorAll('[role="searchbox"],input[type="search"]').length === 1)
  await page.clock.fastForward(10000)
  assert.equal(statusCalls,1,'Slow OAuth requests must not overlap')
  await page.evaluate(() => {window.__hidden=true;document.dispatchEvent(new Event('visibilitychange'))})
  await page.waitForFunction(() => window.__abortedOAuth === 1)
  await page.clock.fastForward(10000)
  assert.equal(statusCalls,1,'Hidden connector page must pause polling')
  releaseFirst()
  const resumed = statusResponse()
  await page.evaluate(() => {window.__hidden=false;document.dispatchEvent(new Event('visibilitychange'))})
  await resumed
  await page.getByRole('button',{name:'Sign out of OAuth',exact:true}).waitFor()
  assert.equal(statusCalls,2,'Visible connector page must resume polling')
  connector = {...connector,oauth:{supported:true,state:'none'}}
  await page.getByRole('button',{name:'Refresh directory',exact:true}).click()
  await page.getByRole('button',{name:'OAuth login',exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:'Sign out of OAuth',exact:true}).count(),0,'Refreshed directory must replace an obsolete polled status')
  await page.evaluate(() => window.clear())
  await page.clock.fastForward(10000)
  assert.equal(statusCalls,2,'Unmounted connector page must stop polling')

  // A newly loaded directory must take precedence over a previously polled state.
  connector = {...connector,oauth:{supported:true,state:'authorized'}}
  await page.setViewportSize({width:1440,height:900})
  await page.evaluate(() => {document.documentElement.classList.add('dark');window.mount()})
  await page.getByText('fixture-connector',{exact:false}).first().waitFor()
  await page.getByRole('button',{name:'Sign out of OAuth',exact:true}).waitFor()
  assert.deepEqual(errors,[])
  await page.close()
  console.log('Connectors: slow OAuth requests never overlap, hidden/unmounted polling stops, visible polling resumes, authoritative directory and narrow/light wide/dark layouts passed')
}, {entry:'/__connectors-entry.js',vite:{plugins:[{name:'connectors-fixture',resolveId(id){if(id==='/__connectors-entry.js')return id},load(id){if(id==='/__connectors-entry.js')return entry},configureServer(server){server.middlewares.use('/__connectors',async (_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__connectors',html))}catch(error){next(error)}})}}]}})
