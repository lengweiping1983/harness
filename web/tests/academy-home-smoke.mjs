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
  // 面板去重：闯关课的实践组件只归舞台所有，面板仅摘要＋主 CTA。
  assert.equal(await panel.locator('[data-academy-section="steps"]').count(), 0)
  await panel.getByRole('button', {name: 'Learn in the focus studio', exact: true}).waitFor()
  // 端到端：回到舞台完成首课（置信度→步骤→自测→反思→结课），核对学习报告由真实证据聚合。
  const en = JSON.parse(await readFile(new URL('../src/i18n/locales/en.json', import.meta.url), 'utf-8'))
  await panel.getByRole('button', {name: en['ui.academy.studio.enter'], exact: true}).click()
  const studio = panel.locator('[data-academy-studio]')
  await studio.waitFor()
  const stepsPhase = studio.locator('[data-studio-phase="steps"]')
  await stepsPhase.waitFor()
  const answerStep = async () => {
    await stepsPhase.locator('[data-confidence="sure"]').click()
    await stepsPhase.locator('button[type="submit"]').click()
  }
  await stepsPhase.getByRole('textbox').fill('Each digit has a job by its place.')
  await answerStep()
  await studio.getByRole('button', {name: en['ui.academy.steps.next'], exact: true}).click()
  await stepsPhase.locator('input[type="number"]').fill('70')
  await answerStep()
  await studio.getByRole('button', {name: en['ui.academy.steps.next'], exact: true}).click()
  await stepsPhase.locator('input[value="b"]').check()
  await answerStep()
  await studio.getByRole('button', {name: en['ui.academy.quickCheck'], exact: true}).click()
  const checksPhase = studio.locator('[data-studio-phase="checks"]')
  await checksPhase.locator('input[value="b"]').check()
  await checksPhase.getByRole('button', {name: en['ui.academy.checkUnderstanding'], exact: true}).click()
  await studio.getByRole('textbox', {name: en['ui.academy.note'], exact: true}).fill('Place gives each digit its value.')
  await studio.getByRole('button', {name: en['ui.academy.completeStudy'], exact: true}).click()
  await studio.locator('[data-academy-report]').waitFor()
  await studio.getByText(en['ui.academy.report.durable'], {exact: true}).waitFor()
  await studio.getByText(en['ui.academy.report.delayed'], {exact: true}).waitFor()
  assert.deepEqual(errors, [])
  console.log('Academy home: grade-band cards and filter, same-instance en/zh/en, course start into default focus studio and exit verified')
} catch (error) {failure = error}
finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend, releases})
}
