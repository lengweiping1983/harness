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
    timer = setTimeout(() => reject(Error('Result fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Result fixture exited: ' + code + diagnostics)))
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
  await editor.fill('raise RuntimeError("SELECTED_RESULT_OUTPUT")\n')
  const executed = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/run'))
  await panel.getByRole('button', {name: '运行代码', exact: true}).click()
  assert.equal((await executed).status(), 200)
  const explain = panel.getByRole('button', {name: '帮我理解这个结果', exact: true})
  await explain.waitFor()
  const runs = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs
  assert.equal(runs.length, 1)
  assert.equal(runs[0].result.status, 'failed')
  assert.ok(runs[0].result.stderr.includes('SELECTED_RESULT_OUTPUT'))
  const draft = 'Keep my own follow-up / 保留草稿'
  await openAcademySection(panel, 'tutor-options')
  await panel.getByRole('textbox', {name: '向带练助手提问', exact: true}).fill(draft)
  await panel.getByRole('checkbox', {name: '允许运行本课代码', exact: true}).check()
  const original = await editor.inputValue()
  await editor.fill(original + '# unsaved\n')
  assert.equal(await explain.isDisabled(), true)
  await panel.getByRole('button', {name: '放弃未保存修改', exact: true}).click()
  let posts = 0
  const endpoint = '**/api/v1/academy/projects/' + project.id + '/tutor-turns'
  await page.route(endpoint, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    posts++
    const request = route.request().postDataJSON()
    assert.equal(request.evidence_run_id, runs[0].id)
    assert.equal(request.allow_execution, false)
    assert.equal(request.require_execution, false)
    assert.equal(request.source, undefined)
    const response = await route.fetch()
    const body = await response.json()
    body.result.tutor_turn.evidence_run_id = 'wrong-record'
    await route.fulfill({response, json: body})
  })
  for (const keyCode of [13, 229]) assert.equal(await explain.evaluate((button, keyCode) => !button.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode, isComposing: keyCode === 13, bubbles: true, cancelable: true})), keyCode), true)
  await explain.dispatchEvent('compositionstart')
  await explain.click()
  assert.equal(posts, 0)
  await explain.dispatchEvent('compositionend')
  await explain.evaluate(button => {button.click(); button.click()})
  await panel.getByRole('alert').waitFor()
  assert.equal(posts, 1)
  const pending = JSON.parse(await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1')))
  assert.equal(pending.tutorInput.evidence_run_id, runs[0].id)
  await page.unroute(endpoint)
  await panel.getByRole('button', {name: '回查操作回执', exact: true}).click()
  await page.waitForFunction(() => !sessionStorage.getItem('harness.academy.pending.v1'))
  await panel.getByLabel('带练回复', {exact: true}).waitFor()
  const turns = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/tutor-turns')).json()).tutor_turns
  assert.equal(turns.length, 1)
  assert.equal(turns[0].status, 'completed')
  assert.equal(turns[0].evidence_run_id, runs[0].id)
  assert.equal(turns[0].allow_execution, false)
  assert.deepEqual(turns[0].actions, [])
  assert.ok(turns[0].response.includes('SELECTED_RESULT_OUTPUT'))
  assert.equal((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs.length, 1)
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    await openAcademySection(panel, 'tutor-evidence')
    await panel.getByText(copy['ui.academy.tutor.resultEvidence'].replace('{id}', runs[0].id), {exact: true}).waitFor()
    for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 1000})
      await page.waitForFunction(width => document.querySelector('.app-shell')?.getAttribute('data-mode') === (width === 390 ? 'overlay' : 'columns'), width)
      if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
      await page.evaluate(theme => document.documentElement.className = theme, theme)
      await openAcademySection(panel, 'tutor-evidence')
    await panel.getByText(copy['ui.academy.tutor.resultEvidence'].replace('{id}', runs[0].id), {exact: true}).waitFor()
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    }
    assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).inputValue(), draft)
    await panel.getByLabel(copy['ui.academy.tutor.answer'], {exact: true}).locator('.md').waitFor()
    assert.ok((await panel.getByLabel(copy['ui.academy.tutor.answer'], {exact: true}).textContent()).includes('SELECTED_RESULT_OUTPUT'))
  }
  assert.equal(posts, 1)
  if (process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await panel.getByRole('button', {name: 'Focus on learning', exact: true}).click()
    await panel.getByRole('button', {name: 'Help me understand this result', exact: true}).scrollIntoViewIfNeeded()
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-recorded-result-en-dark-wide.png'})
  }
  assert.deepEqual(errors, [])
  console.log('Academy result tutoring: real failed sandbox run, exact recorded evidence, read-only tools, no rerun, preserved question, dirty/IME/double-click guards, mismatched receipt recovery and en/zh/en verified')
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
