// 显式在线验收入口，不注册到默认测试，避免无意使用在线凭据。
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {mkdir, readFile} from 'node:fs/promises'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'

if (!process.env.HARNESS_LIVE_API_KEY) throw Error('HARNESS_LIVE_API_KEY is required')
const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.live_model_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
let browser, timer, failure
let diagnostics = ''
backend.stderr.on('data', data => {diagnostics += data})
const lines = createInterface({input: backend.stdout})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Live fixture startup timed out')), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Live fixture exited: ' + code)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch { /* 启动信息不作为模型结果。 */ }})
  })
  clearTimeout(timer)
  browser = await launchBrowser()
  const page = await browser.newPage({viewport: {width: 1440, height: 900}})
  await page.addInitScript(() => {
    window.__diffCopies = []
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
      writeText: async text => {window.__diffCopies.push(text)},
    }})
  })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:' + ready.port)
  const input = page.getByRole('textbox', {name: '输入消息', exact: true})
  await input.fill('请依次完成这个工具验收：1. 使用 bash 执行 python3 -c "import sys; print(\'EXPECTED_FAILURE\'); sys.exit(7)"，这是预期失败，不要重试该命令。2. 使用文件工具创建 verified.txt，内容严格为 harness live ui verified，然后读取确认。3. 最终回复“在线验收完成”。不要执行其他操作。')
  await input.press('Enter')
  await page.locator('[data-turn-completion]').waitFor({timeout: 180000})
  assert.equal((await readFile(ready.workspace + '/verified.txt', 'utf8')).trim(), 'harness live ui verified')
  const failed = page.locator('[data-tool-call-id]').filter({has: page.locator('button.text-danger')})
  assert.ok(await failed.count() >= 1, 'A real failed command must be visible in the transcript')
  const button = failed.first().locator(':scope > button')
  assert.equal(await button.getAttribute('aria-expanded'), 'false')
  assert.equal(await button.locator('.lucide-triangle-alert').count(), 1)
  const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/zh-CN.json', import.meta.url)))
  await page.getByRole('button', {name: translations['ui.components.chat.Conversation.c4ed97b7b12b'].replace('{p0}', '1'), exact: true}).click()
  for (const group of await page.locator('[data-tool-run-turn] > button[aria-expanded="false"]').all()) await group.click()
  const diff = page.locator('figure.diff-view').first()
  for (const tool of await page.locator('[data-tool-call-id]').all()) {
    const disclosure = tool.locator(':scope > button')
    if (await disclosure.getAttribute('aria-expanded') === 'false') await disclosure.click()
    if (await diff.isVisible()) break
  }
  await diff.waitFor()
  await diff.locator('figcaption button').last().click()
  await page.waitForFunction(() => window.__diffCopies.length === 1)
  const copiedDiff = await page.evaluate(() => window.__diffCopies[0])
  assert.match(copiedDiff, /^--- /)
  assert.match(copiedDiff, /\n\+\+\+ /)
  assert.match(copiedDiff, /\n\+harness live ui verified/)
  for (const tool of await page.locator('[data-tool-call-id]').all()) {
    const disclosure = tool.locator(':scope > button')
    if (await disclosure.getAttribute('aria-expanded') === 'true') await disclosure.click()
  }
  for (const language of ['zh-CN', 'en']) {
    const strings = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf8'))
    const response = await page.request.patch('http://127.0.0.1:' + ready.port + '/api/v1/ui-settings', {data: {language}})
    assert.ok(response.ok())
    for (const theme of ['light', 'dark']) {
      for (const width of [390, 1440]) {
        await page.setViewportSize({width, height: 900})
        await page.evaluate(theme => localStorage.setItem('harness.ui-theme', theme), theme)
        await page.reload()
        await page.locator('[data-turn-completion]').waitFor()
        assert.equal(await page.locator('html').getAttribute('lang'), language)
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
        const command = page.locator('[data-tool-call-id]').filter({has: page.locator('button.text-danger')}).first()
        const disclosure = command.locator(':scope > button')
        await disclosure.focus()
        await page.keyboard.press('Enter')
        await command.locator('[id^="tool-detail-"]').waitFor()
        await command.getByText(strings['ui.toolBlock.exitCode'].replace('{code}', '7'), {exact: true}).waitFor()
        await page.keyboard.press('Enter')
        assert.equal(await disclosure.getAttribute('aria-expanded'), 'false')
        if (process.env.HARNESS_TEST_SCREENSHOTS) {
          await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
          await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/live-' + [language, theme, width].join('-') + '.png', animations: 'disabled'})
        }
      }
    }
  }
  assert.deepEqual(errors, [])
  console.log('Live model UI: real command failure, file write/read, original diff copy, reload, keyboard, bilingual themes and narrow layouts passed')
} catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  // 不回显服务端日志，避免供应商错误或意外调试输出泄漏凭据。
  if (diagnostics) console.error('Live backend emitted diagnostics; inspect locally without publishing secrets.')
  await cleanupFixture({error: failure, browser, lines, backend})
}
