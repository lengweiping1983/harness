import {chooseFixtureProject} from './conversation-project.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, diagnostics = ''
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
  for (const width of [390, 1440]) {
    const page = await browser.newPage({viewport: {width, height: 900}})
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(url)
    const input = page.getByRole('textbox', {name: '输入消息', exact: true})
    await input.waitFor()
    await input.evaluate(element => element.blur())
    await page.keyboard.press('ControlOrMeta+Shift+u')
    await chooseFixtureProject(page)
    const text = '起点标识\n' + '长历史原文验证。'.repeat(4000) + '\n唯一搜索标记\n' + '后续内容。'.repeat(4000) + '\n终点标识'
    await input.fill(text)
    await input.press('Enter')
    await page.locator('[data-turn-completion]').last().waitFor()
    await input.fill('保留的搜索验收草稿')
    await page.keyboard.press('Control+f')
    let searchAttempts = 0
    await page.route('**/api/v1/sessions/*/search?*', async route => {
      searchAttempts++
      if (searchAttempts === 1) return route.fulfill({status: 503, json: {error: 'Search temporarily unavailable', code: 'fixture/unavailable'}})
      await route.continue()
    })
    await page.getByRole('textbox', {name: '查找消息内容', exact: true}).fill('唯一搜索标记')
    await page.getByRole('alert').filter({hasText: 'Search temporarily unavailable'}).waitFor()
    await page.getByRole('button', {name: '重试', exact: true}).click()
    const results = page.getByRole('list', {name: '消息查找结果', exact: true})
    let readAttempts = 0
    await page.route('**/api/v1/sessions/*/messages/*?*', async route => {
      readAttempts++
      if (readAttempts === 1) return route.fulfill({status: 503, json: {error: 'Message temporarily unavailable', code: 'fixture/unavailable'}})
      await route.continue()
    })
    await results.getByRole('button').click()
    await page.getByRole('alert').filter({hasText: 'Message temporarily unavailable'}).waitFor()
    await page.getByRole('button', {name: '重试读取', exact: true}).click()
    const reader = page.getByLabel('原文内容', {exact: true})
    await reader.locator('[data-search-match]').waitFor()
    assert.equal(await reader.locator('[data-search-match]').innerText(), '唯一搜索标记')
    assert.ok(await reader.innerText().then(value => value.length <= 16384), 'The reader must use bounded pages')
    const previous = page.getByRole('button', {name: '上一段', exact: true})
    const next = page.getByRole('button', {name: '下一段', exact: true})
    async function turn(button) {
      const response = page.waitForResponse(response => /\/messages\/\d+\?start=/.test(response.url()))
      await button.click()
      const data = await (await response).json()
      await page.waitForFunction(text => document.querySelector('[aria-label="原文内容"]')?.textContent === text, data.text)
    }
    while (await previous.isEnabled()) await turn(previous)
    assert.ok((await reader.innerText()).startsWith('起点标识'))
    while (await next.isEnabled()) await turn(next)
    assert.ok((await reader.innerText()).endsWith('终点标识'))
    await page.getByRole('button', {name: '关闭对话查找', exact: true}).click()
    assert.equal(await input.inputValue(), '保留的搜索验收草稿')
    assert.equal(searchAttempts, 2, 'Search errors must require explicit retry')
    assert.equal(await page.getByRole('alert').count(), 0, 'Recovered errors must clear')
    await page.unroute('**/api/v1/sessions/*/search?*')
    let release, captured
    const pending = new Promise(resolve => {captured = resolve})
    const held = new Promise(resolve => {release = resolve})
    await page.route('**/api/v1/sessions/*/search?*', async route => {
      captured()
      await held
      await route.fulfill({status: 503, json: {error: 'Obsolete search response'}}).catch(() => {})
    })
    await page.keyboard.press('Control+f')
    await page.getByRole('textbox', {name: '查找消息内容', exact: true}).fill('旧请求')
    await pending
    await page.getByRole('button', {name: '关闭对话查找', exact: true}).click()
    release()
    await page.unroute('**/api/v1/sessions/*/search?*')
    await page.keyboard.press('Control+f')
    await page.getByRole('textbox', {name: '查找消息内容', exact: true}).fill('唯一搜索标记')
    await results.getByRole('button').waitFor()
    assert.equal(await page.getByText('Obsolete search response', {exact: true}).count(), 0)
    assert.equal(await page.getByRole('alert').count(), 0)
    await page.getByRole('button', {name: '关闭对话查找', exact: true}).click()
    assert.equal(await input.inputValue(), '保留的搜索验收草稿')
    assert.deepEqual(errors, [])
    await page.close()
  }
  console.log('Conversation search: long persisted message, hit, bounded pages, previous/next, explicit error recovery, closed-request isolation and draft retention passed')

} finally {
  clearTimeout(timer)
  if (browser) await browser.close()
  lines.close()
  if (backend.exitCode === null) {
    const exited = new Promise(resolve => backend.once('exit', resolve))
    backend.kill('SIGTERM')
    await exited
  }
}


