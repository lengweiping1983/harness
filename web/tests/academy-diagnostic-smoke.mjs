import {requireAcademySandbox} from './academy-environment.mjs'
import {openAcademySection} from './academy-sections.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile, mkdir} from 'node:fs/promises'
import {fixturePython} from './python-fixture.mjs'
import {launchBrowser} from './browser-scenario.mjs'
import {cleanupFixture, diagnoseFailure} from './fixture-cleanup.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.academy_web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, page, failure, timer, diagnostics = ''
const releases = []
backend.stderr.on('data', data => {diagnostics += data})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Diagnostic fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Diagnostic fixture exited: ' + code + diagnostics)))
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
  const project = (await (await page.request.get(base + '/api/v1/academy/projects')).json()).projects[0]
  await requireAcademySandbox(project)
  const helper = '# 实验源码 🧪\nprint("BEFORE_LEARNER_FAILURE")\nraise RuntimeError("LEARNER_TRACE")\n'
  const seeded = await page.request.post(base + '/api/v1/academy/projects/' + project.id + '/files', {data: {
    request_id: 'seed-helper', path: 'helper.py', content: helper, expected_version: 'absent', expected_fingerprint: project.fingerprint}})
  assert.equal((await seeded.json()).status, 'ready')
  await panel.getByRole('button', {name: '刷新学习状态', exact: true}).click()
  const file = panel.getByRole('combobox', {name: '选择源码文件', exact: true})
  await file.waitFor()
  await file.selectOption('main.py')
  await editor.fill('import helper\n')
  const executed = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/run'))
  await panel.getByRole('button', {name: '运行代码', exact: true}).click()
  assert.equal((await executed).status(), 200)
  const locate = panel.getByRole('button', {name: '定位到 helper.py 第 3 行', exact: true})
  await locate.waitFor()
  await panel.getByRole('note').filter({hasText: 'RuntimeError: LEARNER_TRACE'}).waitFor()
  assert.ok(await panel.getByText('BEFORE_LEARNER_FAILURE', {exact: true}).isVisible())
  const raw = panel.locator('details').filter({has: page.locator('summary', {hasText: '完整输出与错误堆栈'})})
  assert.equal(await raw.getAttribute('open'), null)
  await raw.locator('summary').click()
  assert.ok((await raw.locator('pre').textContent()).includes('sandbox_entry.py'))
  assert.ok((await raw.locator('pre').textContent()).includes('RuntimeError: LEARNER_TRACE'))
  await raw.locator('summary').click()
  await locate.dispatchEvent('compositionstart')
  await locate.click()
  assert.equal(await file.inputValue(), 'main.py')
  await locate.dispatchEvent('compositionend')
  for (const keyCode of [13, 229]) assert.equal(await locate.evaluate((button, keyCode) => !button.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode, isComposing: keyCode === 13, bubbles: true, cancelable: true})), keyCode), true)
  let release, entered
  const waiting = new Promise(resolve => {entered = resolve})
  const delayed = '**/api/v1/academy/projects/' + project.id + '/source?path=helper.py'
  await page.route(delayed, async route => {
    const response = await route.fetch()
    entered()
    await new Promise(resolve => {release = resolve; releases.push(resolve)})
    await route.fulfill({response})
  })
  await locate.click()
  await waiting
  const question = panel.getByRole('textbox', {name: '向带练助手提问', exact: true})
  await question.fill('I am typing here / 不要抢焦点')
  release()
  const sourceEditor = panel.locator('.academy-workbench-main textarea[spellcheck="false"]')
  await sourceEditor.waitFor()
  await page.waitForFunction(helper => document.querySelector('.academy-workbench-main textarea[spellcheck="false"]')?.value === helper, helper)
  assert.equal(await question.evaluate(node => node === document.activeElement), true, 'A late file load must not take focus from a new question')
  await page.unroute(delayed)
  await file.selectOption('main.py')
  await locate.click()
  await page.waitForFunction(helper => {
    const editor = document.querySelector('.academy-workbench-main textarea[spellcheck="false"]')
    return editor?.value === helper && document.activeElement === editor && editor.value.slice(editor.selectionStart, editor.selectionEnd) === 'raise RuntimeError("LEARNER_TRACE")'
  }, helper)
  assert.equal(await file.inputValue(), 'helper.py')
  await file.selectOption('main.py')
  let releaseHidden, enterHidden, finishHidden
  const hiddenEntered = new Promise(resolve => {enterHidden = resolve})
  const hiddenFinished = new Promise(resolve => {finishHidden = resolve})
  await page.route(delayed, async route => {
    const response = await route.fetch()
    enterHidden()
    await new Promise(resolve => {releaseHidden = resolve; releases.push(resolve)})
    await route.fulfill({response})
    finishHidden()
  })
  await locate.click()
  await hiddenEntered
  await page.keyboard.press('Control+j')
  assert.equal(await page.locator('#task-inspector').getAttribute('aria-hidden'), 'true')
  releaseHidden()
  await hiddenFinished
  await page.unroute(delayed)
  await page.keyboard.press('Control+j')
  await sourceEditor.waitFor()
  assert.equal(await sourceEditor.evaluate(node => node === document.activeElement), false, 'Reopening cannot revive an old source-focus intent')
  await sourceEditor.fill(helper + '# draft\n')
  assert.equal(await locate.isDisabled(), true)
  await panel.getByRole('button', {name: '放弃未保存修改', exact: true}).click()
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 1000})
      await page.waitForFunction(width => document.querySelector('.app-shell')?.getAttribute('data-mode') === (width === 390 ? 'overlay' : 'columns'), width)
      if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
      await page.evaluate(theme => document.documentElement.className = theme, theme)
      await panel.getByRole('button', {name: copy['ui.academy.errorLocation'].replace('{path}', 'helper.py').replace('{line}', '3'), exact: true}).click()
      assert.equal(await sourceEditor.evaluate(node => node.value.slice(node.selectionStart, node.selectionEnd)), 'raise RuntimeError("LEARNER_TRACE")')
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.equal(await panel.locator('[data-academy-run-id]').getByRole('note').textContent(), 'RuntimeError: LEARNER_TRACE')
    }
  }
  await sourceEditor.fill(helper + '# saved later\n')
  await openAcademySection(panel, 'code-options')
  await panel.getByRole('button', {name: 'Save changes', exact: true}).click()
  const oldLocation = panel.getByRole('button', {name: 'Open helper.py, line 3', exact: true})
  await page.waitForFunction(() => document.querySelector('.academy-workbench-main textarea[spellcheck="false"]')?.disabled === false)
  assert.equal(await oldLocation.isDisabled(), true, 'A previous run cannot locate into a different code fingerprint')
  assert.equal((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs.length, 1)
  assert.equal((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/tutor-turns')).json()).tutor_turns.length, 0)
  if (process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await panel.getByRole('button', {name: 'Focus on learning', exact: true}).click()
    await oldLocation.scrollIntoViewIfNeeded()
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-concise-diagnostic-en-dark-wide.png'})
  }
  assert.deepEqual(errors, [])
  console.log('Academy diagnostics: actual Python failure, concise error and stdout, full raw trace, cross-file line selection, late-focus isolation, IME, dirty/stale guards, en/zh/en and responsive themes verified')
} catch (error) {
  failure = error
  if (page) try {
    await diagnoseFailure(error, [async () => {
      const state = await page.evaluate(() => ({language: document.documentElement.lang, inspector: document.querySelector('#task-inspector')?.getAttribute('aria-hidden'),
        textareas: [...document.querySelectorAll('textarea')].map(node => ({label: node.getAttribute('aria-label'), value: node.value, hidden: node.closest('[inert]')?.tagName})),
        text: document.querySelector('#inspector-tab-content')?.textContent?.slice(0,4500)}))
      console.error(state)
    }])
  } catch (diagnostic) {failure = diagnostic}
}
finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend, releases})
}
