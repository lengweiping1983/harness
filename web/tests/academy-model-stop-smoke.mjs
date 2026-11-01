import {openAcademySection} from './academy-sections.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile} from 'node:fs/promises'
import {fixturePython} from './python-fixture.mjs'
import {launchBrowser} from './browser-scenario.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.academy_web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, page, failure, timer, diagnostics = ''
const releases = []
backend.stderr.on('data', data => {diagnostics += data})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Model stop fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Model stop fixture exited: ' + code + diagnostics)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (Number.isInteger(value.port)) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  const base = 'http://127.0.0.1:' + ready.port
  browser = await launchBrowser()
  page = await browser.newPage({viewport: {width: 1440, height: 1000}})
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(base)
  await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  await page.keyboard.press('Control+j')
  const helpGroup = page.locator('#inspector-tab-content[data-panel="home"] details').filter({hasText: '学习与帮助'})
  if ((await helpGroup.getAttribute('open')) === null) await helpGroup.locator('summary').click()
  await page.locator('#inspector-tab-content[data-panel="home"]').getByRole('button', {name: '学习中心', exact: true}).click()
  const panel = page.locator('#inspector-tab-content[data-panel="academy"]')
  await panel.locator('[data-course-id="llm-foundations"]').getByRole('button').click()
  await panel.locator('[data-academy-study-action]').waitFor()
  const get = async path => (await page.request.get(base + '/api/v1/academy' + path)).json()
  const project = (await get('/projects')).projects[0]
  const lesson = (await get('/courses/llm-foundations')).course.lessons.find(item => item.mode === 'model')
  await openAcademySection(panel, 'manage')
  await panel.getByRole('combobox', {name: '课程目录', exact: true}).selectOption(lesson.id)
  await panel.getByRole('button', {name: '保存当前版本并打开本课', exact: true}).click()
  const prompt = panel.getByRole('textbox', {name: '实验提示词', exact: true})
  const original = 'ACADEMY_MODEL_STOP_STREAM / 保留提示草稿'
  await prompt.fill(original)
  let stops = 0, asks = 0
  page.on('request', request => {
    if (request.method() !== 'POST') return
    if (request.url().endsWith('/stop')) stops++
    if (request.url().endsWith('/model-runs')) asks++
  })
  await panel.getByRole('button', {name: '运行提示', exact: true}).click()
  await panel.getByLabel('回答生成中', {exact: true}).getByText('MODEL_STOP_STREAM_VISIBLE', {exact: true}).waitFor()
  const stop = panel.getByRole('button', {name: '停止', exact: true})
  await stop.dispatchEvent('compositionstart')
  await stop.click()
  assert.equal(stops, 0)
  await stop.dispatchEvent('compositionend')
  const run = (await get('/projects/' + project.id + '/model-runs')).model_runs[0]
  const endpoint = '**/api/v1/academy/projects/' + project.id + '/model-runs/' + run.id + '/stop'
  await page.route(endpoint, async route => {await route.fetch(); await route.abort('failed')})
  const lost = page.waitForEvent('requestfailed', request => request.method() === 'POST' && request.url().endsWith('/stop'))
  await stop.evaluate(button => {button.click(); button.click()})
  await lost
  assert.equal(stops, 1)
  const pending = JSON.parse(await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1')))
  assert.deepEqual(pending.modelStopInput, {runId: run.id, requestId: run.request_id})
  await page.unroute(endpoint)
  const receiptEndpoint = '**/api/v1/academy/receipts/' + pending.requestId
  await page.route(receiptEndpoint, async route => {
    const response = await route.fetch(), value = await response.json()
    value.result.model_run.id = 'foreign-model'
    await route.fulfill({response, json: value})
  })
  await panel.getByRole('button', {name: '回查操作回执', exact: true}).click()
  await panel.getByRole('alert').waitFor()
  assert.equal(JSON.parse(await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1'))).requestId, pending.requestId)
  await page.unroute(receiptEndpoint)
  await page.reload()
  await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
  await panel.getByRole('button', {name: '回查操作回执', exact: true}).click()
  await page.waitForFunction(() => sessionStorage.getItem('harness.academy.pending.v1') === null)
  const zh = JSON.parse(await readFile(new URL('../src/i18n/locales/zh-CN.json', import.meta.url), 'utf-8'))
  await panel.locator('[data-model-run-id="' + run.id + '"]').getByText(zh['ui.academy.error.model-interrupted'], {exact: true}).waitFor()
  assert.equal(stops, 1)
  assert.equal(asks, 1)
  assert.equal(await prompt.inputValue(), original)
  assert.equal(await panel.getByLabel('回答生成中', {exact: true}).count(), 0)
  await panel.getByRole('button', {name: '运行提示', exact: true}).click()
  await panel.getByLabel('回答生成中', {exact: true}).getByText('MODEL_STOP_STREAM_VISIBLE', {exact: true}).waitFor()
  const draft = original + ' / newer unsent input'
  await prompt.fill(draft)
  const second = (await get('/projects/' + project.id + '/model-runs')).model_runs[0]
  await stop.click()
  await page.waitForFunction(() => sessionStorage.getItem('harness.academy.pending.v1') === null)
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    await panel.locator('[data-model-run-id="' + second.id + '"]').getByText(copy['ui.academy.error.model-interrupted'], {exact: true}).waitFor()
    assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.modelPrompt'], exact: true}).inputValue(), draft)
  }
  assert.equal(stops, 2)
  assert.equal(asks, 2)
  assert.equal((await get('/projects/' + project.id + '/model-runs')).model_runs[0].prompt, original)
  assert.deepEqual((await get('/projects/' + project.id + '/runs')).runs, [])
  assert.deepEqual(errors, [])
  console.log('Academy model stop: real streaming cancellation, IME/double-click guards, lost and wrong-target receipt recovery, reload, retained newer prompt, no replay or code execution and en/zh/en verified')
} catch (error) {failure = error}
finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend, releases})
}
