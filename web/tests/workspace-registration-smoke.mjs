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
  const errors = [], registrations = [], registered = []
  let directory = '/test-project'
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(base)
  const input = page.getByRole('textbox', {name: translations['ui.components.chat.Composer.a410452649b3'], exact: true})
  await input.waitFor()
  await input.evaluate(element => element.blur())
  await page.keyboard.press('ControlOrMeta+Shift+u')
  await chooseFixtureProject(page)
  await input.fill('existing session setup')
  await input.press('Enter')
  await page.locator('[data-turn-completion]').last().waitFor()
  await page.route('**/api/v1/directories*', route => route.fulfill({json: {path: directory, name: directory.slice(1), parent: '/', entries: [], roots: []}}))
  await page.route('**/api/v1/workspace-options?*', route => route.fulfill({json: {cwd: directory, is_git: false, root: null, branch: null,
    head: null, refs: [], local_branches: [], dirty: null}}))
  await page.route('**/api/v1/workspaces', route => {
    if (route.request().method() === 'POST') {registrations.push(route); return}
    return route.fulfill({json: {workspaces: registered, default: directory}})
  })
  await input.fill('保留现有会话的草稿')
  if (width < 600) await page.getByRole('button', {name: translations['ui.sidebar.expand'], exact: true}).click()
  const open = page.getByRole('button', {name: translations['ui.components.sidebar.ProjectSidebar.79f326be4409'], exact: true})
  await open.click()
  const dialog = page.getByRole('dialog', {name: translations['ui.components.overlays.WorkspacePicker.8964f53ac30f'], exact: true})
  const register = dialog.getByRole('button', {name: translations['ui.components.overlays.WorkspacePicker.9246c4832cf4'], exact: true})
  await page.waitForFunction(() => Array.from(document.querySelectorAll('input')).some(element => element.value === '/test-project'))
  const sent = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/api/v1/workspaces'))
  await register.click()
  await sent
  await dialog.getByRole('button', {name: translations['ui.components.overlays.WorkspacePicker.3fd47edce45b'], exact: true}).click()
  await open.click()
  await dialog.waitFor()
  const record = {id: '/test-project', path: '/test-project', name: 'test-project', color: '#cccccc', exists: true}
  registered.push(record)
  const refreshed = page.waitForResponse(response => response.request().method() === 'GET' && response.url().endsWith('/api/v1/workspaces'))
  await registrations[0].fulfill({json: {workspace: record}})
  await refreshed
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  assert.equal(await dialog.isVisible(), true, 'Late registration must not close a reopened picker')
  assert.equal(registrations.length, 1)
  await dialog.getByRole('button', {name: translations['ui.components.overlays.WorkspacePicker.3fd47edce45b'], exact: true}).click()
  if (width < 600) await page.keyboard.press('Escape')
  assert.equal(await input.inputValue(), '保留现有会话的草稿')
  await page.locator('[data-turn-completion]').last().waitFor()
  directory = '/second-project'
  if (width < 600) await page.getByRole('button', {name: translations['ui.sidebar.expand'], exact: true}).click()
  await open.click()
  await dialog.waitFor()
  const nextSent = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/api/v1/workspaces'))
  await register.click()
  await nextSent
  const second = {...record, id: directory, path: directory, name: directory.slice(1)}
  registered.push(second)
  await registrations[1].fulfill({json: {workspace: second}})
  await dialog.waitFor({state: 'hidden'})
  if (width < 600) await page.keyboard.press('Escape')
  assert.equal(await input.inputValue(), '', 'Current registration must still open a new draft')
  assert.equal(await page.locator('[data-turn-completion]').count(), 0, 'Current registration must leave the previous conversation')
  assert.equal(registrations.length, 2)
  assert.deepEqual(errors, [])
  await page.close()
  }
  console.log('Workspace registration: late success refreshes catalog without closing reopened picker or switching session')
} catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
