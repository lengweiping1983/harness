import {openAcademySection} from './academy-sections.mjs'
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
    timer = setTimeout(() => reject(Error('Course resume fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Course resume fixture exited: ' + code + diagnostics)))
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
  const helpGroup=page.locator('#inspector-tab-content[data-panel="home"] details').filter({hasText:'学习与帮助'})
  if ((await helpGroup.getAttribute('open')) === null) await helpGroup.locator('summary').click()
  await page.locator('#inspector-tab-content[data-panel="home"]').getByRole('button', {name: '学习中心', exact: true}).click()
  const panel = page.locator('#inspector-tab-content[data-panel="academy"]')
  await panel.locator('[data-course-id="harness-foundations"]').getByRole('button').click()
  const editor = panel.getByRole('textbox', {name: '练习代码', exact: true})
  await editor.waitFor()
  const get = async path => (await page.request.get(base + '/api/v1/academy' + path)).json()
  const first = (await get('/projects')).projects[0]
  const original = await editor.inputValue()
  const content = original + '\nprint("RESUME_SAVED_WORK")\n'
  const question = panel.getByRole('textbox', {name: '向带练助手提问', exact: true})
  await question.fill('自己的问题 / my original question')
  await editor.fill(content)
  await openAcademySection(panel, 'manage')
  const python = panel.locator('[data-course-id="python-foundations"]').getByRole('button')
  assert.equal(await python.isDisabled(), true, 'An unsaved draft cannot be replaced by another course')
  assert.equal((await get('/projects')).projects.length, 1)
  await openAcademySection(panel, 'code-options')
  await panel.getByRole('button', {name: '保存修改', exact: true}).click()
  await page.waitForFunction(() => document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
  await python.click()
  await editor.waitFor()
  await page.waitForFunction(id => JSON.parse(localStorage.getItem('harness.academy.view.v1')).projectId !== id, first.id)
  await page.waitForFunction(() => document.querySelector('textarea[aria-label="练习代码"]')?.value.includes('learner ='))
  assert.ok((await editor.inputValue()).includes('learner ='))
  const records = (await get('/projects')).projects
  assert.equal(records.length, 2)
  const saved = (await get('/projects/' + first.id)).project
  let writes = 0
  page.on('request', request => {if (request.method() === 'POST' && request.url().includes('/academy/')) writes++})
  await openAcademySection(panel, 'manage')
  const resume = panel.locator('[data-course-id="harness-foundations"]').getByRole('button', {name: '继续课程：30 天，从零件到 Harness', exact: true})
  await resume.dispatchEvent('compositionstart')
  await resume.click()
  assert.notEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('harness.academy.view.v1')).projectId), first.id)
  await resume.dispatchEvent('compositionend')
  await resume.evaluate(button => {button.click(); button.click()})
  await page.waitForFunction(id => JSON.parse(localStorage.getItem('harness.academy.view.v1')).projectId === id, first.id)
  await page.waitForFunction(expected => document.querySelector('textarea[aria-label="练习代码"]')?.value === expected, content)
  assert.equal(await question.inputValue(), '自己的问题 / my original question')
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    await page.waitForFunction(() => document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    await openAcademySection(panel, 'manage')
    const card = panel.locator('[data-course-id="harness-foundations"]')
    assert.equal(await card.getByRole('button').textContent(), copy['ui.academy.resumeCourseAction'])
    assert.equal(await card.getByText(copy['ui.academy.resumeProjectName'].replace('{name}', first.name), {exact: true}).isVisible(), true)
    for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 1000})
      await page.evaluate(theme => document.documentElement.className = theme, theme)
      await page.locator('.app-shell[data-mode="' + (width === 390 ? 'overlay' : 'columns') + '"]').waitFor()
      // 悬停等待真实布局稳定，不点击只读导航；加载后的可用状态单独确认。
      await card.getByRole('button').hover()
      await page.waitForFunction(() => document.querySelector('[data-course-id="harness-foundations"] button')?.disabled === false)
      assert.equal(await card.getByRole('button').isEnabled(), true)
      assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.source'], exact: true}).inputValue(), content)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    }
  }
  assert.equal(writes, 0, 'Resuming and inspecting records must not create, save, run or call a model')
  assert.equal((await get('/projects')).projects.length, 2)
  assert.deepEqual((await get('/projects/' + first.id)).project, saved)
  assert.deepEqual(errors, [])
  console.log('Academy resume: existing project and saved work, own question, dirty and IME guards, double-click read-only navigation, unchanged records and en/zh/en theme/width matrix verified')
} catch (error) {failure = error}
finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend, releases})
}
