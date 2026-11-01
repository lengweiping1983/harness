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
    timer = setTimeout(() => reject(Error('Tutor stop fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Tutor stop fixture exited: ' + code + diagnostics)))
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
  await panel.locator('[data-course-id="harness-foundations"]').getByRole('button').click()
  const editor = panel.getByRole('textbox', {name: '练习代码', exact: true})
  await editor.waitFor()
  const get = async path => (await page.request.get(base + '/api/v1/academy' + path)).json()
  const project = (await get('/projects')).projects[0]
  const source = await editor.inputValue()
  const question = panel.getByRole('textbox', {name: '向带练助手提问', exact: true})
  await question.fill('ACADEMY_STOP_STREAM / 保留我的问题')
  let stops = 0, asks = 0
  page.on('request', request => {
    if (request.method() !== 'POST') return
    if (request.url().endsWith('/stop')) stops++
    if (request.url().endsWith('/tutor-turns')) asks++
  })
  await panel.getByRole('button', {name: '发送', exact: true}).click()
  await panel.getByLabel('回答生成中', {exact: true}).getByText('STOP_STREAM_VISIBLE', {exact: true}).waitFor()
  const stop = panel.getByRole('button', {name: '停止', exact: true})
  await stop.dispatchEvent('compositionstart')
  await stop.click()
  assert.equal(stops, 0)
  await stop.dispatchEvent('compositionend')
  const turn = (await get('/projects/' + project.id + '/tutor-turns')).tutor_turns[0]
  const endpoint = '**/api/v1/academy/projects/' + project.id + '/tutor-turns/' + turn.id + '/stop'
  await page.route(endpoint, async route => {await route.fetch(); await route.abort('failed')})
  const lost = page.waitForEvent('requestfailed', request => request.method() === 'POST' && request.url().endsWith('/stop'))
  await stop.evaluate(button => {button.click(); button.click()})
  await lost
  assert.equal(stops, 1)
  const pending = JSON.parse(await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1')))
  assert.deepEqual(pending.tutorStopInput, {turnId: turn.id, requestId: turn.request_id})
  await page.unroute(endpoint)
  const receiptEndpoint = '**/api/v1/academy/receipts/' + pending.requestId
  await page.route(receiptEndpoint, async route => {
    const response = await route.fetch(), value = await response.json()
    value.result.tutor_turn.id = 'foreign-turn'
    await route.fulfill({response, json: value})
  })
  await panel.getByRole('button', {name: '回查操作回执', exact: true}).click()
  await panel.getByRole('alert').waitFor()
  assert.equal(JSON.parse(await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1'))).requestId, pending.requestId, 'A foreign turn cannot acknowledge this stop')
  await page.unroute(receiptEndpoint)
  await page.reload()
  await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
  await panel.getByRole('button', {name: '回查操作回执', exact: true}).click()
  await page.waitForFunction(() => sessionStorage.getItem('harness.academy.pending.v1') === null)
  await panel.getByText('本次带练已中断，结果未确认，不会自动重新执行。', {exact: true}).waitFor()
  assert.equal(stops, 1)
  assert.equal(asks, 1)
  assert.equal(await question.inputValue(), 'ACADEMY_STOP_STREAM / 保留我的问题')
  assert.equal(await panel.getByLabel('回答生成中', {exact: true}).count(), 0)
  assert.equal((await get('/projects/' + project.id + '/tutor-turns')).tutor_turns[0].status, 'unknown')
  await panel.getByRole('button', {name: '发送', exact: true}).click()
  await panel.getByLabel('回答生成中', {exact: true}).getByText('STOP_STREAM_VISIBLE', {exact: true}).waitFor()
  const draft = source + '\n# 停止时保留的草稿 / keep this draft\n'
  const second = (await get('/projects/' + project.id + '/tutor-turns')).tutor_turns[0]
  await editor.fill(draft)
  await stop.click()
  await page.waitForFunction(() => sessionStorage.getItem('harness.academy.pending.v1') === null)
  await panel.getByRole('button', {name: '保存并发送', exact: true}).waitFor()
  assert.equal(await editor.inputValue(), draft)
  assert.equal((await get('/projects/' + project.id + '/source?path=main.py')).content, source)
  assert.deepEqual((await get('/projects/' + project.id + '/runs')).runs, [])
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    await panel.locator('[data-tutor-turn-id="' + second.id + '"]').getByText(copy['ui.academy.error.tutor-interrupted'], {exact: true}).waitFor()
    assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).inputValue(), 'ACADEMY_STOP_STREAM / 保留我的问题')
  }
  assert.equal(stops, 2)
  assert.equal(asks, 2)
  assert.deepEqual(errors, [])
  console.log('Academy stop: real native streaming cancellation, IME/double-click guards, lost and mismatched receipt recovery without replay, reload, retained question/dirty source, no execution and same-instance en/zh/en verified')
} catch (error) {failure = error}
finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend, releases})
}
