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
    const original = window.fetch
    window.__directorySignals = []
    window.fetch = (input, options) => {
      if (String(input).includes('/api/v1/directories')) window.__directorySignals.push(options?.signal)
      return original(input, options)
    }
  })
  const held = []
  await page.route('**/api/v1/directories*', route => {held.push(route)})
  await page.goto(base)
  const input = page.getByRole('textbox', {name: translations['ui.components.chat.Composer.a410452649b3'], exact: true})
  await input.waitFor()
  if (width < 600) await page.getByRole('button', {name: translations['ui.sidebar.expand'], exact: true}).click()
  const open = page.getByRole('button', {name: translations['ui.components.sidebar.ProjectSidebar.79f326be4409'], exact: true})
  await open.click()
  const dialog = page.getByRole('dialog', {name: translations['ui.components.overlays.WorkspacePicker.8964f53ac30f'], exact: true})
  await dialog.waitFor()
  await page.waitForFunction(() => window.__directorySignals.length === 1)
  await dialog.getByRole('button', {name: translations['ui.components.overlays.WorkspacePicker.3fd47edce45b'], exact: true}).click()
  await dialog.waitFor({state: 'hidden'})
  await page.waitForFunction(() => window.__directorySignals[0]?.aborted === true, undefined, {timeout: 3000})
  await open.click()
  await dialog.waitFor()
  await page.waitForFunction(() => window.__directorySignals.length === 2)
  await held[1].fulfill({json: {path: '/current-project', name: 'current-project', parent: '/', entries: [], roots: []}})
  const pathInput = dialog.getByRole('textbox', {name: translations['ui.workspacePicker.path'], exact: true})
  await page.waitForFunction(label => Array.from(document.querySelectorAll('input')).some(element => element.getAttribute('aria-label') === label && element.value === '/current-project'), translations['ui.workspacePicker.path'])
  await held[0].fulfill({json: {path: '/obsolete-project', name: 'obsolete-project', parent: '/', entries: [], roots: []}}).catch(() => {})
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  assert.ok((await dialog.innerText()).includes('current-project'))
  assert.ok(!(await dialog.innerText()).includes('obsolete-project'))
  await pathInput.fill('/next-project')
  for (const native of [{isComposing:true}, {keyCode:229}]) {
    const blocked = await pathInput.evaluate((element, native) => {
      const event = new KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true, ...native})
      element.dispatchEvent(event)
      return event.defaultPrevented
    }, native)
    assert.equal(blocked, true, 'IME Enter must prevent implicit path-form submission')
  }
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  assert.equal(await page.evaluate(() => window.__directorySignals.length), 2, 'IME must not navigate directories')
  assert.equal(await pathInput.inputValue(), '/next-project')
  await pathInput.press('Enter')
  await page.waitForFunction(() => window.__directorySignals.length === 3)
  await dialog.getByRole('button', {name: translations['ui.components.overlays.WorkspacePicker.6bff6a717a8c'], exact: true}).click()
  await page.waitForFunction(() => window.__directorySignals.length === 4 && window.__directorySignals[2].aborted)
  await held[3].fulfill({json: {path: '/latest-project', name: 'latest-project', parent: '/', entries: [], roots: []}})
  await page.waitForFunction(() => Array.from(document.querySelectorAll('input')).some(element => element.value === '/latest-project'))
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  assert.deepEqual(errors, [])
  await page.close()
  }
  console.log('Workspace picker: closing aborts directory reads; reopened panel rejects obsolete responses')
} catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
