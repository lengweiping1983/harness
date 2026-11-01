import {chooseFixtureProject} from './conversation-project.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, failure, diagnostics = ''
const releases = []

function checkpoint(label) {
  const {promise, resolve} = Promise.withResolvers()
  return {promise, resolve, wait: async () => {
    let timeout
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timeout = setTimeout(() => reject(Error('Route checkpoint timed out: ' + label)), 3000)
      })])
    } finally {clearTimeout(timeout)}
  }}
}

async function interruptedRead(page, pattern, json, stale) {
  const started = checkpoint(pattern + ' started')
  const oldResponse = checkpoint(pattern + ' old response')
  const oldFinished = checkpoint(pattern + ' old settled')
  const resumedDispatch = checkpoint(pattern + ' resumed dispatch')
  const resumedHandling = checkpoint(pattern + ' resumed handling')
  const handleResumed = checkpoint(pattern + ' allow resumed handler')
  releases.push(() => {oldResponse.resolve(); handleResumed.resolve()})
  let dispatched = 0, handled = 0, firstURL
  await page.route(pattern, async route => {
    const ordinal = ++dispatched
    if (ordinal === 2) {
      resumedDispatch.resolve()
      // 固定请求事件先到、拦截处理稍后的顺序，避免偶发才覆盖这个窗口。
      await handleResumed.promise
    }
    handled++
    if (ordinal === 1) {firstURL = route.request().url(); started.resolve(); await oldResponse.promise}
    if (ordinal === 2) resumedHandling.resolve()
    await route.fulfill({json: ordinal === 1 ? stale : json})
    if (ordinal === 1) oldFinished.resolve()
  })
  return {started: started.wait, resume: async message => {
    await page.keyboard.press('Control+j')
    await page.waitForFunction(() => document.getElementById('task-inspector')?.getAttribute('aria-hidden') === 'true')
    await page.waitForFunction(url => window.planReads.some(read => read.url === url && read.aborted), firstURL)
    oldResponse.resolve()
    await oldFinished.wait()
    assert.equal(dispatched, 1, 'Hidden plans must not start another read after the cancelled response settles')
    const resumed = page.waitForRequest(pattern, {timeout: 3000})
    await page.keyboard.press('Control+j')
    await resumed
    await resumedDispatch.wait()
    assert.equal(handled, 1, 'Request event precedes the deliberately held route handler')
    handleResumed.resolve()
    await resumedHandling.wait()
    assert.ok(handled >= 2, message)
  }}
}
backend.stderr.on('data', data => {diagnostics += data})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Fixture exited: ' + code + '\n' + diagnostics)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  browser = await launchBrowser()
  const url = 'http://127.0.0.1:' + ready.port
  const page = await browser.newPage({viewport: {width: 1440, height: 900}})
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window)
    window.planReads = []
    window.fetch = (input, options) => {
      const url = new URL(typeof input === 'string' ? input : input.url ?? String(input), location.href)
      if (/\/api\/v1\/sessions\/[^/]+\/plans(?:\/|$)/.test(url.pathname)) {
        const read = {url: url.href, aborted: false}
        window.planReads.push(read)
        const signal = options?.signal ?? input.signal
        signal?.addEventListener('abort', () => {read.aborted = true}, {once: true})
      }
      return fetch(input, options)
    }
  })
  await page.goto(url)
  const input = page.getByRole('textbox', {name: '输入消息', exact: true})
  await chooseFixtureProject(page)
  await input.fill('plan visibility fixture request')
  await input.press('Enter')
  await page.getByText('fixture response verified', {exact: true}).waitFor()
  const plan = {id: 'visibility-plan', title: 'Visibility plan', status: 'approved', feedback: '', previous_id: null, seq: 1, submitted_at: 1, decided_at: 2,
    plan: '# Visibility plan', diff: '', implementation: [{id: 'read', content: 'Read evidence', status: 'completed', evidence: [{call_id: 'visibility-read', name: 'read', source_seq: 19, summary: 'README summary', truncated: true}]}]}
  const listPattern = '**/api/v1/sessions/*/plans'
  const list = await interruptedRead(page, listPattern, {plans: []}, {plans: [{...plan, id: 'stale-plan', title: 'Stale plan'}]})
  await page.keyboard.press('Control+2')
  await page.waitForFunction(() => document.querySelector('#inspector-tab-content[data-panel="todos"]'))
  await list.started()
  await list.resume('Reopening plans must reload a request cancelled while hidden')
  await page.locator('#inspector-tab-content').getByText('计划将保存在这里，可回看批准版本和修改意见。', {exact: true}).waitFor()
  assert.equal(await page.getByText('Stale plan', {exact: true}).count(), 0)
  await page.unroute(listPattern)
  await page.route('**/api/v1/sessions/*/plans', route => route.fulfill({json: {plans: [plan]}}))
  const detail = await interruptedRead(page, '**/api/v1/sessions/*/plans/visibility-plan',
    {plan}, {plan: {...plan, plan: '# Stale plan details', implementation: []}})
  await page.getByRole('button', {name: '刷新计划历史', exact: true}).click()
  await detail.started()
  await detail.resume('Plan details must resume after hiding')
  await page.locator('#inspector-tab-content').getByRole('heading', {name: 'Visibility plan', exact: true}).waitFor()
  assert.equal(await page.getByRole('heading', {name: 'Stale plan details', exact: true}).count(), 0)
  const evidence = page.locator('#inspector-tab-content details').filter({hasText: 'README summary'})
  await evidence.locator('summary').click()
  const original = await interruptedRead(page, '**/api/v1/sessions/*/plans/visibility-plan/evidence/19?offset=0',
    {evidence: {call_id: 'visibility-read', name: 'read', source_seq: 19, text: 'Original verified evidence', offset: 0, next_offset: null, total_chars: 26}},
    {evidence: {call_id: 'visibility-read', name: 'read', source_seq: 19, text: 'Stale original evidence', offset: 0, next_offset: null, total_chars: 23}})
  await evidence.getByRole('button', {name: '查看原始工具记录', exact: true}).click()
  await original.started()
  await original.resume('Expanded evidence must resume after hiding')
  await evidence.getByText('Original verified evidence', {exact: true}).waitFor()
  assert.equal(await evidence.getByText('Stale original evidence', {exact: true}).count(), 0)
  assert.deepEqual(errors, [])
  console.log('Plan visibility: interrupted list, detail and expanded evidence reload when reopened')
} catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend, releases})
}
