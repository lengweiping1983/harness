import {chooseFixtureProject} from './conversation-project.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {cleanupFixture} from './fixture-cleanup.mjs'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile} from 'node:fs/promises'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, failure, diagnostics = ''
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
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(() => {
    window.__replyCopies = []
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async text => {window.__replyCopies.push(text)}}})
  })
  await page.route('**/api/v1/sessions/*/messages/*', async route => {
    const response = await route.fetch()
    const data = await response.json()
    await route.fulfill({json: {...data, text: 'short'}})
  })
  let releaseCompletion
  const completionGate = new Promise(resolve => {releaseCompletion = resolve})
  await page.route('**/completion?offset=*', async route => {await completionGate; await route.continue()})
  await page.goto(base)
  const input = page.getByRole('textbox', {name: translations['ui.components.chat.Composer.a410452649b3'], exact: true})
  await input.waitFor()
  await input.evaluate(element => element.blur())
  await page.keyboard.press('ControlOrMeta+Shift+u')
  await chooseFixtureProject(page)
  await input.fill('reply copy fixture request ' + language)
  await input.press('Enter')
  const completion = page.locator('[data-turn-completion]').last()
  await completion.waitFor()
  const copy = completion.getByRole('button', {name: translations['ui.components.chat.blocks.TurnCompletionBar.d784b8b766ea'], exact: true})
  await completion.getByRole('status').filter({hasText: translations['ui.turnCompletion.loading']}).waitFor()
  assert.equal(await completion.getAttribute('aria-busy'), 'true')
  assert.equal(await copy.isDisabled(), true)
  releaseCompletion()
  await completion.getByRole('status').filter({hasText: translations['ui.turnCompletion.loading']}).waitFor({state: 'hidden'})
  assert.equal(await completion.getAttribute('aria-busy'), 'false')
  await copy.click()
  await completion.getByRole('alert').filter({hasText: translations['ui.lib.turnCompletion.cf5425f62d93']}).waitFor()
  assert.deepEqual(await page.evaluate(() => window.__replyCopies), [], 'Invalid reply fragments must never reach the clipboard')
  await page.unroute('**/api/v1/sessions/*/messages/*')
  await completion.getByRole('button', {name: translations['ui.components.chat.blocks.TurnCompletionBar.fa660d296961'], exact: true}).click()
  await page.waitForFunction(() => window.__replyCopies.length === 1)
  assert.deepEqual(await page.evaluate(() => window.__replyCopies), ['fixture response verified'])
  assert.equal(await completion.getByRole('alert').count(), 0)
  assert.deepEqual(errors, [])
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  await page.close()
  }
  console.log('Reply copy: inconsistent fragments blocked before clipboard; read-only retry copies the complete saved reply')
} catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
