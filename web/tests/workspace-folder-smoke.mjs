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
  const errors = [], mkdirRequests = [], directoryPaths = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/directories*', async route => {
    const path = new URL(route.request().url()).searchParams.get('path') || '/test-project'
    directoryPaths.push(path)
    await route.fulfill({json: {path, name: path.split('/').pop(), parent: '/', entries: [], roots: []}})
  })
  await page.route('**/api/v1/workspaces/mkdir', route => {mkdirRequests.push(route)})
  await page.goto(base)
  const input = page.getByRole('textbox', {name: translations['ui.components.chat.Composer.a410452649b3'], exact: true})
  await input.waitFor()
  await input.fill('保留未发送草稿')
  if (width < 600) await page.getByRole('button', {name: translations['ui.sidebar.expand'], exact: true}).click()
  const open = page.getByRole('button', {name: translations['ui.components.sidebar.ProjectSidebar.79f326be4409'], exact: true})
  await open.click()
  const dialog = page.getByRole('dialog', {name: translations['ui.components.overlays.WorkspacePicker.8964f53ac30f'], exact: true})
  const create = dialog.getByRole('button', {name: translations['ui.components.overlays.WorkspacePicker.84244abc71de'], exact: true})
  await create.waitFor()
  await page.waitForFunction(() => Array.from(document.querySelectorAll('input')).some(element => element.value === '/test-project'))
  await create.click()
  const folderName = dialog.getByPlaceholder(translations['ui.components.overlays.WorkspacePicker.ae51764fbb38'], {exact: true})
  await folderName.fill('new-folder')
  for (const key of ['Enter', 'Escape']) {
    await folderName.evaluate((element, key) => element.dispatchEvent(new KeyboardEvent('keydown', {key, isComposing:true, bubbles:true, cancelable:true})), key)
    await folderName.evaluate((element, key) => element.dispatchEvent(new KeyboardEvent('keydown', {key, keyCode:229, bubbles:true, cancelable:true})), key)
  }
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  assert.equal(mkdirRequests.length, 0, 'IME confirmation must not create a folder')
  assert.equal(await folderName.isVisible(), true, 'IME Escape must not dismiss the folder editor')
  assert.equal(await folderName.inputValue(), 'new-folder')
  const sent = page.waitForRequest(request => request.url().endsWith('/api/v1/workspaces/mkdir'))
  await folderName.press('Enter')
  await sent
  await folderName.press('Enter')
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  assert.equal(mkdirRequests.length, 1, 'Repeated Enter must not submit the same pending folder creation twice')

  await dialog.getByRole('button', {name: translations['ui.components.overlays.WorkspacePicker.3fd47edce45b'], exact: true}).click()
  await open.click()
  await dialog.waitFor()
  await page.waitForFunction(() => Array.from(document.querySelectorAll('input')).some(element => element.value === '/test-project'))
  await mkdirRequests[0].fulfill({json: {created: '/test-project/new-folder', name: 'new-folder'}})
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  assert.equal(directoryPaths.includes('/test-project/new-folder'), false, 'Completed work from a closed panel must not navigate the reopened panel')
  assert.equal(await dialog.isVisible(), true)
  await page.waitForFunction(label => Array.from(document.querySelectorAll('button')).some(element => element.textContent === label && !element.disabled), translations['ui.components.overlays.WorkspacePicker.84244abc71de'])
  await create.click()
  await folderName.fill('current-folder')
  const currentRequest = page.waitForRequest(request => request.url().endsWith('/api/v1/workspaces/mkdir'))
  await folderName.press('Enter')
  await currentRequest
  assert.equal(mkdirRequests.length, 2, 'The completed old operation must release its submission lock')
  await mkdirRequests[1].fulfill({json: {created: '/test-project/current-folder', name: 'current-folder'}})
  await page.waitForFunction(() => Array.from(document.querySelectorAll('input')).some(element => element.value === '/test-project/current-folder'))
  await dialog.getByRole('button', {name: translations['ui.components.overlays.WorkspacePicker.3fd47edce45b'], exact: true}).click()
  if (width < 600) await page.keyboard.press('Escape')
  assert.equal(await input.inputValue(), '保留未发送草稿')
  assert.deepEqual(errors, [])
  await page.close()
  }
  console.log('Workspace folder: repeated Enter submits once; late completion preserves reopened picker and draft')
} catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
