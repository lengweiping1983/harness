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
  const page = await browser.newPage({viewport: {width: 1440, height: 900}})
  await page.goto(url)
  const input = page.getByRole('textbox', {name: '输入消息', exact: true})
  await chooseFixtureProject(page)
  await input.fill('plan visibility fixture request')
  await input.press('Enter')
  await page.getByText('fixture response verified', {exact: true}).waitFor()
  let requests = 0, release
  const held = new Promise(resolve => {release = resolve})
  await page.route('**/api/v1/sessions/*/comparison/options?*', route => route.fulfill({json: {branches: ['main'], current: 'main', upstream: null, commits: [], next_offset: null}}))
  await page.route('**/api/v1/sessions/*/comparison?*', async route => {
    requests++
    if (requests === 1) await held
    await route.fulfill({json: {available: true, scope: 'uncommitted', left: 'HEAD', right: 'WORKTREE', revision: String(requests), workspace: '.', total: 0, files: [], next_offset: null, added: 0, deleted: 0}}).catch(() => {})
  })
  await page.getByRole('button', {name: '启动器', exact: true}).click()
  await page.getByRole('button', {name: '变更', exact: true}).click()
  const deadline = Date.now() + 10000
  while (!requests && Date.now() < deadline) await page.waitForTimeout(50)
  assert.equal(requests, 1)
  await page.keyboard.press('Control+j')
  await page.waitForFunction(() => document.getElementById('task-inspector')?.getAttribute('aria-hidden') === 'true')
  release()
  await page.waitForTimeout(200)
  const fresh = page.waitForRequest('**/api/v1/sessions/*/comparison?*', {timeout: 3000})
  await page.keyboard.press('Control+j')
  await fresh
  assert.equal(requests, 2, 'Changes must reload an interrupted comparison when reopened')
  const endpoints = page.locator('.changes-endpoints.changes-compare-picker')
  await endpoints.waitFor()
  assert.equal(await endpoints.evaluate(node => node.firstElementChild?.tagName), 'SELECT', 'Changes must show the comparison base before the worktree')
  console.log('Changes visibility: interrupted comparison reloads when reopened')
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


