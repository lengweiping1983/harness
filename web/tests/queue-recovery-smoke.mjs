import {chooseFixtureProject} from './conversation-project.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {once} from 'node:events'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile} from 'node:fs/promises'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, diagnostics = ''
backend.stderr.on('data', value => {diagnostics += value})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Queue fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Queue fixture exited: ' + code)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch { /* 启动日志不是就绪回执。 */ }})
  })
  clearTimeout(timer)
  browser = await launchBrowser()
  const base = 'http://127.0.0.1:' + ready.port
  for (const [language, theme, width] of [['zh-CN', 'light', 390], ['en', 'dark', 1440]]) {
  const page = await browser.newPage({viewport: {width, height: 900}})
  const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url)))
  assert.ok((await page.request.patch(base + '/api/v1/ui-settings', {data: {language}})).ok())
  await page.addInitScript(theme => localStorage.setItem('harness.ui-theme', theme), theme)
  const errors = []
  let sessionId, failRead = false
  page.on('pageerror', error => errors.push(error.message))
  page.on('websocket', socket => {sessionId = new URL(socket.url()).searchParams.get('session')})
  await page.route('**/api/v1/sessions/*/queue', route => failRead
    ? route.fulfill({status: 503, json: {error: 'QUEUE_READ_RECOVERY', code: 'fixture/unavailable'}})
    : route.continue())
  await page.goto(base)
  const input = page.getByRole('textbox', {name: translations['ui.components.chat.Composer.a410452649b3'], exact: true})
  await input.waitFor()
  await input.evaluate(element => element.blur())
  await page.keyboard.press('ControlOrMeta+Shift+u')
  await chooseFixtureProject(page)
  await input.fill('queue fixture setup')
  await input.press('Enter')
  await page.getByText('fixture response verified', {exact: true}).waitFor()
  assert.ok(sessionId)
  const paused = await page.request.patch(base + '/api/v1/sessions/' + sessionId + '/queue-control', {data: {paused: true}})
  assert.ok(paused.ok())
  await input.fill('queued message must remain pending')
  await input.press('Enter')
  const queued = page.locator('[data-queued-id]').filter({hasText: 'queued message must remain pending'})
  await queued.waitFor()
  await input.fill('unsent draft survives read recovery')
  failRead = true
  const readError = page.getByRole('alert').filter({hasText: 'QUEUE_READ_RECOVERY'})
  await readError.waitFor()
  failRead = false
  await page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/queue') && response.status() === 200)
  await readError.waitFor({state: 'hidden', timeout: 5000})
  await queued.waitFor()
  assert.equal(await input.inputValue(), 'unsent draft survives read recovery')
  // 自动读取恢复不能把一次操作失败误报成成功，也不能重发写请求。
  let deletes = 0
  await page.route('**/api/v1/sessions/*/runs/*?expected_version=*', async route => {
    if (route.request().method() !== 'DELETE') return route.continue()
    deletes++
    await route.fulfill({status: 409, json: {error: 'QUEUE_ACTION_CONFLICT', code: 'fixture/conflict'}})
  })
  await queued.getByRole('button', {name: translations['ui.catalog.removeNamed'].replace('{name}', 'queued message must remain pending'), exact: true}).click()
  assert.equal(deletes, 0)
  await queued.getByRole('group', {name: translations['ui.queue.confirmRemove'], exact: true}).getByRole('button', {name: translations['ui.catalog.confirmRemove'], exact: true}).click()
  const actionError = page.getByRole('alert').filter({hasText: 'QUEUE_ACTION_CONFLICT'})
  await actionError.waitFor()
  await page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/queue') && response.status() === 200)
  assert.equal(await actionError.isVisible(), true)
  assert.equal(deletes, 1)
  assert.equal(await input.inputValue(), 'unsent draft survives read recovery')
  assert.deepEqual(errors, [])
  assert.equal(await page.locator('html').getAttribute('lang'), language)
  assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  await page.close()
  }
  console.log('Queue recovery: successful reads clear read errors, retain action conflicts and draft, and never replay writes')
} finally {
  clearTimeout(timer)
  try {await browser?.close()} finally {
    lines.close()
    if (backend.exitCode === null && backend.signalCode === null) {
      const exited = once(backend, 'exit')
      backend.kill('SIGTERM')
      const deadline = setTimeout(() => backend.kill('SIGKILL'), 20000)
      await exited.finally(() => clearTimeout(deadline))
    }
  }
}
