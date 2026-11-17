import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile} from 'node:fs/promises'
import {fixturePython} from './python-fixture.mjs'
import {launchBrowser} from './browser-scenario.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.academy_web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, page, failure, timer, diagnostics = ''
const releases = []
backend.stderr.on('data', data => {diagnostics += data})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Home fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Home fixture exited: ' + code + diagnostics)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (Number.isInteger(value.port)) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  const base = 'http://127.0.0.1:' + ready.port
  browser = await launchBrowser()
  page = await browser.newPage({viewport: {width: 1440, height: 1000}})
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(base)
  await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  await page.keyboard.press('Control+j')
  const helpGroup = page.locator('#inspector-tab-content[data-panel="home"] details').filter({hasText: '学习与帮助'})
  if ((await helpGroup.getAttribute('open')) === null) await helpGroup.locator('summary').click()
  await page.locator('#inspector-tab-content[data-panel="home"]').getByRole('button', {name: '学习中心', exact: true}).click()
  const panel = page.locator('#inspector-tab-content[data-panel="academy"]')
  const home = panel.locator('[data-academy-home]')
  await home.waitFor()
  // 学段筛选：小学/初中卡片与徽标，筛选后隐藏另一学段。
  await home.locator('[data-course-id="primary-math-foundations"]').waitFor()
  await home.locator('[data-course-id="middle-school-math"]').waitFor()
  assert.equal(await home.locator('[data-course-id="primary-math-foundations"]').getByText('小学', {exact: true}).count(), 1)
  assert.equal(await home.locator('[data-course-id="middle-school-math"]').getByText('初中', {exact: true}).count(), 1)
  await home.locator('[data-home-band="primary"]').click()
  assert.equal(await home.locator('[data-course-id="middle-school-math"]').count(), 0)
  await home.locator('[data-home-band="middle"]').click()
  assert.equal(await home.locator('[data-course-id="primary-math-foundations"]').count(), 0)
  await home.locator('[data-home-band="all"]').click()
  await home.locator('[data-course-id="primary-math-foundations"]').waitFor()
  await home.locator('[data-course-id="middle-school-math"]').waitFor()
  // 同实例语言矩阵：主页文案随语言解析，卡片身份不变。
  for (const language of ['en', 'zh-CN', 'en']) {
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    await home.getByText(copy['ui.academy.home.title'], {exact: true}).waitFor()
    await home.getByRole('button', {name: copy['ui.academy.home.band.primary'], exact: true}).waitFor()
    assert.equal(await home.locator('[data-course-id="primary-math-foundations"]').count(), 1)
  }
  // 选课进入项目：闯关课默认进入专注舞台，可退出回面板。
  await home.locator('[data-course-id="primary-math-foundations"]').getByRole('button').click()
  await panel.locator('[data-academy-studio]').waitFor()
  await panel.locator('[data-academy-studio-exit]').click()
  assert.equal(await panel.locator('[data-academy-studio]').count(), 0)
  assert.equal(await panel.locator('[data-academy-home]').count(), 0)
  await panel.getByRole('heading', {name: /Lesson 1 ·/}).first().waitFor()
  assert.deepEqual(errors, [])
  console.log('Academy home: grade-band cards and filter, same-instance en/zh/en, course start into default focus studio and exit verified')
} catch (error) {failure = error}
finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend, releases})
}
