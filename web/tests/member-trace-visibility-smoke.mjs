import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ExecutionTracePanel} from '/src/components/inspector/ExecutionTracePanel.tsx'
import {TeamMemberDetails} from '/src/components/chat/blocks/TeamMemberDetails.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.reads = []
api.executionTrace = async () => ({ok:true,data:{entries:[{id:'tool-1',kind:'tool',name:'Fixture tool',status:'completed',time:1,order:1}],has_more:false,next_after:null,context_missing:false,session_id:'owner'}})
api.executionTracePart = (_session,_entry,_part,_offset,_agent,signal) => new Promise(resolve => window.reads.push({signal,resolve}))
api.subagentDetail = async () => ({ok:true,data:{agent:{agent_id:'member',child_session_id:'child',status:'completed',created_at:1,tool_calls:0,pending_messages:0,prompt:'Original task 原文',result:'Original result'}}})
api.getSession = async () => ({ok:false,message:'Fixture conversation unavailable'})
const root = createRoot(document.getElementById('root'))
window.configure = (view,active,language,theme) => {
  setLocale(language); document.documentElement.className = theme
  root.render(React.createElement(view === 'member' ? TeamMemberDetails : ExecutionTracePanel,
    view === 'member' ? {key:view,owner:'owner',id:'member',name:'Member',embedded:true,active} : {key:view,sessionId:'owner',active}))
}
window.finish = (index,text) => window.reads[index].resolve({ok:true,data:{text,offset:0,total_chars:text.length,next_offset:null}})
`
const html = '<!doctype html><html><body><div id="root"></div><script type="module" src="/__member-trace-entry.js"></script></body></html>'
await browserScenario(async ({server,browser}) => {
  const errors = []
  for (const language of ['en','zh-CN']) for (const theme of ['light','dark']) for (const width of [390,1440]) {
    const strings = JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))
    for (const view of ['trace','member']) {
      const page = await browser.newPage({viewport:{width,height:900}})
      page.on('pageerror',error => errors.push(error.message))
      await page.goto(server.resolvedUrls.local[0]+'__member-trace')
      await page.waitForFunction(() => typeof window.configure === 'function')
      await page.evaluate(args => window.configure(...args),[view,true,language,theme])
      if (view === 'member') await page.getByRole('button',{name:strings['ui.team.member.traces.show'],exact:true}).click()
      const summary = page.locator('summary').filter({hasText:strings['ui.trace.toolDetails']})
      await summary.click()
      await page.waitForFunction(() => window.reads.length === 1)
      await page.evaluate(args => window.configure(...args),[view,false,language,theme])
      await page.waitForFunction(() => window.reads[0].signal.aborted)
      await page.evaluate(() => window.finish(0,'STALE HIDDEN RESULT'))
      await page.evaluate(() => new Promise(requestAnimationFrame))
      assert.equal(await page.getByText('STALE HIDDEN RESULT',{exact:true}).count(),0)
      assert.equal(await page.evaluate(() => window.reads.length),1)
      await page.evaluate(args => window.configure(...args),[view,true,language,theme])
      await page.waitForFunction(() => window.reads.length === 2)
      await page.evaluate(() => window.finish(1,'CURRENT RESULT 原文'))
      await page.getByText('CURRENT RESULT 原文',{exact:true}).waitFor()
      await summary.click()
      await page.waitForFunction(() => window.reads[1].signal.aborted)
      await page.close()
    }
  }
  assert.deepEqual(errors,[])
  console.log('Member traces: hidden tool reads cancel, ignore late results and resume across two entry points and eight locale/theme/width combinations')
},{entry:'/__member-trace-entry.js',vite:{plugins:[{
  name:'member-trace-fixture',
  resolveId(id) {if (id === '/__member-trace-entry.js') return id},
  load(id) {if (id === '/__member-trace-entry.js') return entry},
  configureServer(server) {server.middlewares.use('/__member-trace',async (_request,response,next) => {
    try {response.setHeader('Content-Type','text/html');response.end(await server.transformIndexHtml('/__member-trace',html))}
    catch (error) {next(error)}
  })},
}]}})
