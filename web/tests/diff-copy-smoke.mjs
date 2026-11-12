import {chooseFixtureProject} from './conversation-project.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {cleanupFixture, diagnoseFailure} from './fixture-cleanup.mjs'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile} from 'node:fs/promises'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.file_review_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, activePage, failure, diagnostics = ''
backend.stderr.on('data', data => {diagnostics += data})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('File review fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('File review fixture exited: ' + code)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch { /* 启动诊断不是就绪回执。 */ }})
  })
  clearTimeout(timer)
  browser = await launchBrowser()
  const base = 'http://127.0.0.1:' + ready.port
  for (const [language, theme, width] of [['zh-CN', 'light', 390], ['en', 'dark', 1440]]) {
    const page = await browser.newPage({viewport: {width, height: 900}})
    activePage = page
    const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url)))
    assert.ok((await page.request.patch(base + '/api/v1/ui-settings', {data: {language}})).ok())
    await page.addInitScript(theme => {
      localStorage.setItem('harness.ui-theme', theme)
      window.__copies = []
      window.__clipboardMode = 'success'
      window.__fallbackCalls = 0
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async text => {
        if (window.__clipboardMode !== 'success') throw new DOMException('Clipboard access denied', 'NotAllowedError')
        window.__copies.push(text)
      }}})
      document.execCommand = command => {
        assertCopy(command)
        window.__fallbackCalls++
        if (window.__clipboardMode === 'throw') throw new DOMException('Legacy copy failed', 'NotAllowedError')
        if (window.__clipboardMode !== 'fallback') return false
        window.__copies.push(document.activeElement.value)
        return true
      }
      function assertCopy(command) {if (command !== 'copy') throw Error('Unexpected command: ' + command)}
    }, theme)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(base)
    const input = page.getByRole('textbox', {name: translations['ui.components.chat.Composer.a410452649b3'], exact: true})
    await input.waitFor()
    await input.evaluate(element => element.blur())
    await page.keyboard.press('ControlOrMeta+Shift+u')
    await chooseFixtureProject(page)
    await input.fill('file review fixture request ' + language)
    await input.press('Enter')
    await page.locator('[data-turn-completion]').waitFor()
    await page.getByRole('button', {name: translations['ui.components.chat.Conversation.c4ed97b7b12b'].replace('{p0}', '1'), exact: true}).click()
    const tool = page.locator('[data-tool-call-id="fixture-file-write"]')
    await tool.locator(':scope > button').click()
    const diff = tool.locator('figure.diff-view')
    await diff.waitFor()
    const copy = diff.locator('figcaption button').last()
    await copy.focus()
    await page.keyboard.press('Enter')
    await copy.filter({has: page.locator('.text-ok')}).waitFor()
    const copied = await page.evaluate(() => window.__copies[0])
    assert.match(copied, /^--- /, 'Copied differences must preserve the original file header')
    assert.match(copied, /\n\+\+\+ /)
    assert.match(copied, /\n\+first\n\+second\n\+third/)
    await page.evaluate(() => {window.__clipboardMode = 'fallback'})
    await copy.click()
    await page.waitForFunction(() => window.__fallbackCalls === 1)
    assert.deepEqual(await page.evaluate(() => window.__copies), [copied, copied])
    assert.equal(await copy.evaluate(element => element === document.activeElement), true, 'Fallback copying must restore keyboard focus')
    await page.evaluate(() => {window.__clipboardMode = 'denied'})
    await copy.click()
    await page.getByText(translations['ui.components.chat.blocks.MessageActions.753d8bb0da99'], {exact: true}).waitFor()
    assert.equal(await copy.locator('.text-ok').count(), 0, 'A failed copy must not retain a success icon')
    assert.equal(await page.evaluate(() => window.__fallbackCalls), 2)
    const textareas = await page.locator('textarea').count()
    await page.evaluate(() => {window.__clipboardMode = 'throw'})
    await copy.click()
    await page.waitForFunction(() => window.__fallbackCalls === 3)
    assert.equal(await page.locator('textarea').count(), textareas, 'A throwing fallback must remove its temporary textarea')
    assert.equal(await copy.evaluate(element => element === document.activeElement), true)
    assert.deepEqual(errors, [])
    assert.equal(await page.locator('html').getAttribute('lang'), language)
    assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await page.close()
  }
  console.log('Diff copy: original headers, keyboard copy, fallback, failure feedback and bilingual layouts passed')
} catch (error) {
  try {
    await diagnoseFailure(error, [async () => {
      if (activePage && !activePage.isClosed()) console.error((await activePage.locator('body').innerText()).slice(-10000))
    }])
  } catch (diagnosed) { failure = diagnosed }
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
