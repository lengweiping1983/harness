import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {readFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, failure, diagnostics = ''
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
  for (const language of ['zh-CN', 'en']) {
    const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url)))
    assert.ok((await (await browser.newContext()).request.patch(url + '/api/v1/ui-settings', {data: {language}})).ok())
    for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
      const page = await browser.newPage({viewport: {width, height: 900}})
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      await page.addInitScript(({theme}) => localStorage.setItem('harness.ui-theme', theme), {theme})
      await page.goto(url)
      await page.locator('.composer-editor').waitFor()
      await page.locator('.composer-editor').evaluate(element => element.blur())
      await page.keyboard.press('ControlOrMeta+Shift+u')
      await page.keyboard.press('Control+3')
      const panel = page.locator('#inspector-tab-content[data-panel="context"]')
      await panel.getByText(translations['ui.context.noSession.title'], {exact: true}).waitFor()
      await panel.getByText(translations['ui.context.noSession.description'], {exact: true}).waitFor()
      assert.equal(await panel.getByRole('alert').count(), 0)
      assert.ok(await panel.evaluate(element => element.scrollWidth <= element.clientWidth + 1), 'Empty context must fit ' + [language, theme, width].join('/'))
      assert.equal(await page.locator('html').getAttribute('lang'), language)
      assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
      assert.deepEqual(errors, [])
      await page.close()
    }
  }
  console.log('Context empty state: Chinese/English, light/dark, narrow/wide passed')
} catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
