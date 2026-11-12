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
  let release, forkedId, forkCount = 0, captured
  const intercepted = new Promise(resolve => {captured = resolve})
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/sessions/*/fork', async route => {
    forkCount++
    const response = await route.fetch()
    forkedId = (await response.json()).session.id
    await new Promise(resolve => {release = resolve; captured()})
    await route.fulfill({response})
  })
  await page.goto(base)
  const input = page.getByRole('textbox', {name: translations['ui.components.chat.Composer.a410452649b3'], exact: true})
  await input.waitFor()
  await input.evaluate(element => element.blur())
  await page.keyboard.press('ControlOrMeta+Shift+u')
  await chooseFixtureProject(page)
  const title = 'fork navigation fixture ' + language
  await input.fill(title)
  await input.press('Enter')
  await page.locator('[data-turn-completion]').last().waitFor()
  if (width < 600) await page.getByRole('button', {name: translations['ui.sidebar.expand'], exact: true}).click()
  await page.locator('#project-sidebar').getByRole('button', {name: title, exact: true}).click({button: 'right'})
  await page.getByRole('menuitem', {name: translations['ui.components.sidebar.ProjectSidebar.90d990618b19'], exact: true}).click()
  let interceptTimer
  try {
    await Promise.race([intercepted, new Promise((_, reject) => {interceptTimer = setTimeout(() => reject(Error('Fork request not intercepted')), 10000)})])
  } finally {clearTimeout(interceptTimer)}

  if (width < 600) await page.keyboard.press('Escape')
  await input.evaluate(element => element.blur())
  await page.keyboard.press('ControlOrMeta+Shift+u')
  await input.fill('new draft must keep focus and content')
  const refreshed = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/v1/sessions')
  release()
  await refreshed
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  assert.equal(await input.inputValue(), 'new draft must keep focus and content', 'Late fork must not replace the new draft with the forked conversation')
  assert.equal(await page.locator('[data-turn-completion]').count(), 0)
  const catalog = await page.request.get(base + '/api/v1/sessions')
  assert.ok((await catalog.json()).sessions.some(session => session.id === forkedId), 'The successfully created fork must remain in the catalog')
  assert.equal(forkCount, 1)
  await page.unroute('**/api/v1/sessions/*/fork')
  if (width < 600) await page.getByRole('button', {name: translations['ui.sidebar.expand'], exact: true}).click()
  await page.locator('#project-sidebar').getByRole('button', {name: title, exact: true}).first().click({button: 'right'})
  const currentFork = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/fork'))
  await page.getByRole('menuitem', {name: translations['ui.components.sidebar.ProjectSidebar.90d990618b19'], exact: true}).click()
  assert.equal((await currentFork).status(), 201)
  if (width < 600) await page.keyboard.press('Escape')
  await page.locator('[data-turn-completion]').last().waitFor()
  assert.equal(await input.inputValue(), '', 'A current fork request must still select its child conversation')
  assert.deepEqual(errors, [])
  await page.close()
  }
  console.log('Sidebar fork: late successful result preserves new draft and retains created child in catalog')
} catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
