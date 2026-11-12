import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React, {useState} from 'react'
import {createRoot} from 'react-dom/client'
import {AcademyTutor} from '/src/components/inspector/AcademyTutor.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale = setLocale; window.writes = 0
const turn = {id:'turn',request_id:'request',project_id:'project',lesson_id:'lesson',input_fingerprint:'a'.repeat(64),prompt:'原来的问题 / original question',allow_execution:true,require_execution:false,response:'',model:'fixture',language:'en',status:'running',actions:[],created_at:1}
const lesson = {id:'lesson',mode:'python',order:1,title:'Lesson',goal:'Goal',reading:'Reading',milestone:false,run_command:['python','main.py'],source_manifest:[],references:[],prerequisites:[],trace_labels:{}}
function App() {
 const [value, setValue] = useState(turn)
 window.phase = (tool, status='running', terminal=false) => setValue({...turn,status:terminal?'completed':'running',response:terminal?'Original reply 原文':'',actions:tool?[{tool,status,output:''}]:[]})
 window.stopping = () => setValue({...turn,stopping:true,partial_response:'Do not keep generating'})
 window.partial = text => setValue({...turn,partial_response:text})
 window.failure = code => setValue({...turn,allow_execution:false,status:code==='tutor-limit'?'failed':'unknown',error_code:code,completed_at:2,
   response:Array.from({length:120},(_,index)=>'Returned fragment / 已返回片段 '+index).join('\\n\\n'),proposal_path:'main.py',
   actions:[{tool:'propose_edit',status:'completed',output:'',proposal:{path:'main.py',before:'answer = 1\\n',after:'answer = 2\\n',expected_version:'a'.repeat(64),diff:'--- a/main.py\\n+++ b/main.py\\n@@ -1 +1 @@\\n-answer = 1\\n+answer = 2\\n'}}]})
 return React.createElement('main',{className:'mx-auto max-w-xl p-4'},React.createElement(AcademyTutor,{projectId:'project',lesson,fingerprint:turn.input_fingerprint,dirty:false,turns:[value],disabled:false,onSuggest:()=>window.writes++,onTry:()=>window.writes++,onAsk:()=>window.writes++,onStop:()=>window.writes++,onRefresh:()=>window.writes++}))
}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
const html = '<html><body><div id="root"></div><script type="module" src="/__academy-status-entry.js"></script></body></html>'
await browserScenario(async ({server, browser}) => {
  for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    const page = await browser.newPage({viewport: {width, height: 1000}})
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(server.resolvedUrls.local[0] + '__academy-status')
    await page.waitForFunction(() => typeof window.phase === 'function')
    await page.evaluate(theme => document.documentElement.className = theme, theme)
    await page.getByRole('textbox').fill('自己的草稿 / my draft')
    await page.getByRole('textbox').focus()
    const questionNode = await page.getByRole('textbox').elementHandle()
    for (const language of ['en', 'zh-CN', 'en']) {
      await page.evaluate(language => window.locale(language), language)
      const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
      await page.evaluate(() => window.phase(null))
      await page.getByRole('status').filter({hasText: copy['ui.academy.tutor.running']}).waitFor()
      for (const text of ['第一段 / first part', '第一段 / first part，第二段 / second part']) {
        await page.evaluate(text => window.partial(text), text)
        const partial = page.getByLabel(copy['ui.academy.tutor.partial'], {exact: true})
        await partial.getByText(text, {exact: true}).waitFor()
        assert.equal(await partial.getAttribute('aria-busy'), 'true')
        assert.equal(await page.getByLabel(copy['ui.academy.tutor.answer'], {exact: true}).count(), 0)
        assert.equal(await questionNode.evaluate(node => node === document.activeElement), true)
      }
      for (const tool of ['inspect_file', 'run_example', 'check_lesson', 'propose_edit']) {
        await page.evaluate(tool => window.phase(tool), tool)
        await page.getByRole('status').filter({hasText: copy['ui.academy.tutor.working.' + tool]}).waitFor()
        assert.equal(await page.locator('[data-academy-section="tutor-evidence"]').getAttribute('open'), null)
        assert.equal(await page.getByRole('textbox').inputValue(), '自己的草稿 / my draft')
        assert.equal(await questionNode.evaluate(node => node === document.activeElement), true)
        await page.evaluate(tool => window.phase(tool, 'completed'), tool)
        await page.getByRole('status').filter({hasText: copy['ui.academy.tutor.running']}).waitFor()
      }
      await page.evaluate(() => window.phase('run_example', 'unknown'))
      await page.getByRole('status').filter({hasText: copy['ui.academy.error.outcome-unknown']}).waitFor()
      assert.equal(await page.getByRole('status').evaluate(node => node.classList.contains('text-warn')), true)
      await page.evaluate(() => window.stopping())
      await page.getByRole('status').filter({hasText: copy['ui.academy.tutor.stopping']}).waitFor()
      assert.equal(await page.getByRole('button', {name: copy['ui.academy.tutor.stoppingAction'], exact: true}).isDisabled(), true)
      assert.equal(await page.getByLabel(copy['ui.academy.tutor.partial'], {exact: true}).count(), 0)
      await page.evaluate(() => window.phase('run_example', 'completed', true))
      await page.getByLabel(copy['ui.academy.tutor.answer'], {exact: true}).waitFor()
      assert.equal(await page.getByRole('status').count(), 0, 'A completed turn cannot keep claiming that a tool is running')
      assert.equal(await page.getByLabel(copy['ui.academy.tutor.partial'], {exact: true}).count(), 0)
      assert.equal(await page.getByLabel(copy['ui.academy.tutor.answer'], {exact: true}).textContent(), 'Original reply 原文')
      for(const code of ['tutor-interrupted','tutor-timeout','tutor-limit']){
        await page.evaluate(code=>window.failure(code),code)
        const notice=page.getByRole('status').filter({hasText:copy['ui.academy.error.'+code]})
        await notice.waitFor()
        assert.equal(await notice.evaluate((node,label)=>!!(node.compareDocumentPosition(document.querySelector('[aria-label="'+label+'"]'))&Node.DOCUMENT_POSITION_FOLLOWING),copy['ui.academy.tutor.answer']),true)
        assert.equal(await notice.evaluate((node,label)=>!!(node.compareDocumentPosition(document.querySelector('[aria-label="'+label+'"]'))&Node.DOCUMENT_POSITION_FOLLOWING),copy['ui.academy.tutor.proposal']),true)
        assert.ok((await page.getByLabel(copy['ui.academy.tutor.answer'],{exact:true}).textContent()).includes('已返回片段 119'))
        assert.equal(await page.getByRole('button',{name:copy['ui.academy.tutor.tryProposal'],exact:true}).isDisabled(),true)
        assert.equal(await page.getByRole('textbox').inputValue(),'自己的草稿 / my draft')
        assert.equal(await questionNode.evaluate(node=>node===document.activeElement),true)
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    }
    assert.equal(await page.evaluate(() => window.writes), 0)
    assert.deepEqual(errors, [])
    await page.close()
  }
  console.log('Academy tutor status: recorded tool phases, completed-tool versus completed-turn distinction, visible unknown outcome, collapsed details, preserved focus/draft, no writes and same-instance en/zh/en responsive themes verified')
}, {entry: '/__academy-status-entry.js', vite: {plugins: [{
  name: 'academy-status-fixture', resolveId(id) {if (id === '/__academy-status-entry.js') return id}, load(id) {if (id === '/__academy-status-entry.js') return entry},
  configureServer(server) {server.middlewares.use('/__academy-status', async (_req, res, next) => {try {res.setHeader('Content-Type', 'text/html'); res.end(await server.transformIndexHtml('/__academy-status', html))} catch (error) {next(error)}})},
}]}})
