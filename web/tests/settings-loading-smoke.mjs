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
backend.stderr.on('data', value => {diagnostics += value})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(new Error('Fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(new Error('Fixture exited: ' + code)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  browser = await launchBrowser()
  const page = await browser.newPage({viewport: {width: 390, height: 900}})
  let pending, entered
  const requested = new Promise(resolve => {entered = resolve})
  await page.route('**/assets/AutomationsPage-*.js', route => {pending = route; entered()})
  await page.goto('http://127.0.0.1:' + ready.port)
  await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  const start = performance.now()
  await page.keyboard.press('Control+,')
  const content = page.locator('#settings-content')
  await content.locator('#language-preference').waitFor({timeout: 2000})
  assert.equal(await content.getByText(/当前：.*系统地区：/).count(), 0)
  const openMs = performance.now() - start
  assert.equal(await content.getByRole('status').filter({hasText: '正在加载设置'}).count(), 0)
  if (await page.getByRole('button', {name: '高级与自动化', exact: true}).getAttribute('aria-expanded') === 'false') await page.getByRole('button', {name: '高级与自动化', exact: true}).click()
  await page.getByRole('button', {name: '自动化', exact: true}).click()
  await Promise.race([requested, new Promise((_, reject) => setTimeout(() => reject(new Error('Lazy page request missing')), 5000))])
  await content.getByRole('status').waitFor()
  await page.getByRole('button', {name: '通用', exact: true}).click()
  await content.locator('#language-preference').waitFor({timeout: 2000})
  if (await page.getByRole('button', {name: '高级与自动化', exact: true}).getAttribute('aria-expanded') === 'false') await page.getByRole('button', {name: '高级与自动化', exact: true}).click()
  await page.getByRole('button', {name: '自动化', exact: true}).click()
  await content.getByRole('alert').waitFor({timeout: 13000})
  assert.equal(await content.getByRole('status').count(), 0, 'A stalled module must stop showing loading')
  await page.getByRole('button', {name: '通用', exact: true}).click()
  await content.locator('#language-preference').waitFor({timeout: 2000})
  await pending.abort('failed')
  await page.keyboard.press('Control+,')
  await page.keyboard.press('Control+,')
  await content.locator('#language-preference').waitFor({timeout: 2000})
  const failedPage = await browser.newPage()
  await failedPage.route('**/assets/AutomationsPage-*.js', route => route.abort('failed'))
  await failedPage.goto('http://127.0.0.1:' + ready.port)
  await failedPage.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  await failedPage.keyboard.press('Control+,')
  await failedPage.locator('#language-preference').waitFor({timeout: 2000})
  if (await failedPage.getByRole('button', {name: '高级与自动化', exact: true}).getAttribute('aria-expanded') === 'false') await failedPage.getByRole('button', {name: '高级与自动化', exact: true}).click()
  await failedPage.getByRole('button', {name: '自动化', exact: true}).click()
  await failedPage.locator('#settings-content').getByRole('alert').waitFor({timeout: 5000})
  await failedPage.getByRole('button', {name: '通用', exact: true}).click()
  await failedPage.locator('#language-preference').waitFor({timeout: 2000})
  await failedPage.close()
  console.log('Settings shell: first open ' + Math.round(openMs) + ' ms; stalled page timeout, navigation and reopen passed')
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
