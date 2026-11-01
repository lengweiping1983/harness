import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ContextAuditPanel} from '/src/components/inspector/ContextAuditPanel.tsx'
import {ExecutionTracePanel} from '/src/components/inspector/ExecutionTracePanel.tsx'
import {ContextCompactControl} from '/src/components/inspector/ContextCompactControl.tsx'
import {RuntimeTab} from '/src/components/inspector/RuntimeTab.tsx'
import {ContextSettingsPanel} from '/src/components/overlays/ContextSettingsPanel.tsx'
import {AgentsPanel} from '/src/components/overlays/AgentsPanel.tsx'
import {McpServicePanel} from '/src/components/overlays/McpServicePanel.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t;setLocale('en')
useAppStore.setState({activeSessionId:'fixture'})
const views={audit:[ContextAuditPanel,{sessionId:'fixture',agentId:'member'}],trace:[ExecutionTracePanel,{sessionId:'fixture'}],compact:[ContextCompactControl,{sessionId:'fixture',onComplete:()=>{}}],runtime:[RuntimeTab,{}],settings:[ContextSettingsPanel,{}],agents:[AgentsPanel,{onNavigate:()=>{}}],mcp:[McpServicePanel,{}]}
const [Component,props]=views[new URLSearchParams(location.search).get('view')]
createRoot(document.getElementById('root')).render(React.createElement(Component,props))
`

await browserScenario(async ({server,browser}) => {
  for (const view of ['audit','trace','compact','runtime','settings','agents','mcp']) {
    const page = await browser.newPage()
    page.setDefaultTimeout(15000)
    await page.clock.install()
    const requests = [], errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/v1/**', route => {
      requests.push({method:route.request().method(),url:route.request().url()})
      return route.fulfill({status:200,body:'not json',contentType:'application/json'})
    })
    await page.goto(server.resolvedUrls.local[0]+'__panel-error?view='+view)
    if (view === 'compact') {
      const label = await page.evaluate(() => window.copy('ui.context.compact'))
      await page.getByRole('button',{name:label,exact:true}).click()
    }
    const initial = await page.evaluate(() => window.copy('ui.http.invalidResponse'))
    await page.getByText(initial,{exact:true}).waitFor()
    // 暂停点取自浏览器自身时钟，避免宿主取整或调度差异产生倒退。
    await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000))
    const before = requests.length
    for (const locale of ['en','zh-CN','en']) for (const theme of ['light','dark']) for (const width of [390,1440]) {
      await page.setViewportSize({width,height:900})
      await page.evaluate(({locale,theme}) => {window.locale(locale);document.documentElement.className=theme},{locale,theme})
      const expected = await page.evaluate(() => window.copy('ui.http.invalidResponse'))
      await page.getByText(expected,{exact:true}).waitFor()
      assert.equal(requests.length,before,view+': language changes must not repeat operations')
    }
    assert.deepEqual(errors,[],view)
    if (view === 'compact') {
      assert.equal(requests.filter(request=>request.method==='POST').length,1)
      assert.ok(requests.some(request=>request.method==='GET' && request.url.includes('request_id=')))
    }
    await page.close()
    console.log(view+': saved protocol error follows language; operations are not repeated')
  }
},{entry:'/__panel-error-entry.js',vite:{plugins:[{
  name:'panel-error-language-fixture',
  resolveId(id){if(id==='/__panel-error-entry.js')return id},
  load(id){if(id==='/__panel-error-entry.js')return entry},
  configureServer(server){server.middlewares.use('/__panel-error',async(req,res,next)=>{
    try {
      res.setHeader('Content-Type','text/html')
      res.end(await server.transformIndexHtml('/__panel-error','<html><body><div id="root"></div><script type="module" src="/__panel-error-entry.js"></script></body></html>'))
    } catch(error){next(error)}
  })},
}]}})
