import {requireAcademySandbox} from './academy-environment.mjs'
import {academyDiagnostics} from './academy-diagnostics.mjs'
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
let browser, page, failure, timer, backendDiagnostics = ''
const releases = []
backend.stderr.on('data', data => {backendDiagnostics += data})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Tutor fixture startup timed out: ' + backendDiagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Tutor fixture exited: ' + code + backendDiagnostics)))
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
  const initial = await editor.inputValue()
  const changed = initial + '\nprint("NATIVE_TUTOR_OBSERVED_OUTPUT")\n'
  await editor.fill(changed)
  const question = 'Run my code and help me reason about it / 用户原文 {input}'
  await openAcademySection(panel, 'tutor-options')
  await panel.getByRole('textbox', {name: '向带练助手提问', exact: true}).fill(question)
  await panel.getByRole('checkbox', {name: '允许运行本课代码', exact: true}).check()
  let posts = 0
  const endpoint = '**/api/v1/academy/projects/' + project.id + '/tutor-turns'
  await page.route(endpoint, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    posts++
    assert.equal(route.request().postDataJSON().source.content, changed)
    await route.fetch()
    await route.abort('failed')
  })
  const send = panel.getByRole('button', {name: '保存并发送', exact: true})
  for (const keyCode of [13, 229]) {
    assert.equal(await send.evaluate((button, keyCode) => !button.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode, isComposing: keyCode === 13, bubbles: true, cancelable: true})), keyCode), true)
  }
  await send.dispatchEvent('compositionstart')
  await send.click()
  assert.equal(posts, 0)
  await send.dispatchEvent('compositionend')
  const lost = page.waitForEvent('requestfailed', request => request.method() === 'POST' && request.url().endsWith('/tutor-turns'))
  await send.evaluate(button => {button.click(); button.click()})
  await lost
  assert.equal(posts, 1)
  await page.unroute(endpoint)
  const pending = await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1'))
  assert.ok(pending)
  await page.reload()
  await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
  await panel.getByRole('button', {name: '回查操作回执', exact: true}).click()
  await panel.getByLabel('带练回复', {exact: true}).waitFor()
  await page.waitForFunction(() => !sessionStorage.getItem('harness.academy.pending.v1'))
  const turns = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/tutor-turns')).json()).tutor_turns
  assert.equal(turns.length, 1)
  assert.equal(turns[0].status, 'completed')
  assert.equal(turns[0].prompt, question)
  assert.equal(turns[0].allow_execution, true)
  const executionDiagnostics = academyDiagnostics(browser)
  await executionDiagnostics.wait()
  const observed = executionDiagnostics.snapshot().filter(record => record.method === 'GET'
    && record.url === base + '/api/v1/academy/projects/' + project.id + '/tutor-turns' && record.state === 'received')
    .flatMap(record => JSON.parse(record.body).tutor_turns)
    .find(turn => turn.id === turns[0].id && turn.status === 'completed')
  assert.ok(observed, 'Background tutor execution must retain its actual completed history response')
  assert.deepEqual(observed.actions, turns[0].actions)
  assert.ok(turns[0].response.includes('NATIVE_TUTOR_OBSERVED_OUTPUT'))
  const runs = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs
  assert.equal(runs.length, 1)
  assert.equal(turns[0].actions.at(-1).run_id, runs[0].id)
  assert.equal(turns[0].input_fingerprint, runs[0].input_fingerprint)
  assert.equal(runs[0].kind, 'run')
  const saved = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id)).json()).project
  assert.equal(saved.progress[0].state, 'in_progress', 'A tutor example cannot masquerade as a passed lesson check')
  assert.equal(await editor.inputValue(), changed)
  const answerMaterial = async response => {
    await response.locator('.md').waitFor()
    return {paragraphs: await response.locator('p').allTextContents(), code: await response.locator('pre code').allTextContents()}
  }
  const answer = await answerMaterial(panel.getByLabel('带练回复', {exact: true}))
  let laterPosts = 0
  page.on('request', request => {if (request.method() === 'POST' && request.url().endsWith('/tutor-turns')) laterPosts++})
  const followup = 'My next question / 新草稿 🧪'
  await panel.getByRole('textbox', {name: '向带练助手提问', exact: true}).fill(followup)
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
      await panel.getByRole('heading', {name: copy['ui.academy.tutor.title'], exact: true}).waitFor()
      assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).inputValue(), followup)
      assert.deepEqual(await answerMaterial(panel.getByLabel(copy['ui.academy.tutor.answer'], {exact: true})), answer)
      const bounds = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}))
      assert.ok(bounds.scroll <= bounds.width, [language, theme, width].join('/'))
    }
  }
  assert.equal(laterPosts, 0)
  // 隐藏时取消尚未完成的读取；迟到响应不得重新发布。
  let seen, release, handled
  const entered = new Promise(resolve => {seen = resolve})
  const gate = new Promise(resolve => {release = resolve})
  const done = new Promise(resolve => {handled = resolve})
  releases.push(release)
  await page.route(endpoint, async route => {
    seen()
    await gate
    try {await route.fulfill({json: {tutor_turns: [{...turns[0], response: 'STALE_TUTOR_RESPONSE'}]}})}
    catch (error) {if (!route.request().failure()) throw error}
    finally {handled()}
  })
  await openAcademySection(panel, 'tutor-options')
  await panel.getByRole('button', {name: 'Refresh tutoring history', exact: true}).click()
  await entered
  const cancelled = page.waitForEvent('requestfailed', request => request.method() === 'GET' && request.url().endsWith('/tutor-turns'))
  await page.keyboard.press('Control+j')
  await cancelled
  release()
  await done
  await page.unroute(endpoint)
  await page.keyboard.press('Control+j')
  await panel.getByLabel('Tutor response', {exact: true}).waitFor()
  assert.equal(await panel.getByText('STALE_TUTOR_RESPONSE', {exact: true}).count(), 0)
  assert.equal(await panel.getByRole('textbox', {name: 'Ask about this lesson', exact: true}).inputValue(), followup)
  assert.equal(laterPosts, 0)
  const created = await (await page.request.post(base + '/api/v1/academy/projects', {data: {request_id: 'other-learning-project', course_id: project.course_id}})).json()
  const other = created.result.project
  await panel.getByRole('button', {name: 'Refresh learning state', exact: true}).click()
  const manage = panel.locator('summary').filter({hasText: 'Courses and projects'}).first()
  if (await manage.locator('..').getAttribute('open') === null) await manage.click()
  const choice = panel.getByRole('combobox', {name: 'My learning projects', exact: true})
  await choice.selectOption(other.id)
  await page.waitForFunction(id => JSON.parse(localStorage.getItem('harness.academy.view.v1')).projectId === id, other.id)
  await panel.getByRole('textbox', {name: 'Ask about this lesson', exact: true}).waitFor()
  assert.equal(await panel.getByRole('textbox', {name: 'Ask about this lesson', exact: true}).inputValue(), '')
  assert.equal(await panel.getByLabel('Tutor response', {exact: true}).count(), 0)
  await choice.selectOption(project.id)
  await panel.getByLabel('Tutor response', {exact: true}).waitFor()
  assert.equal(await panel.getByRole('textbox', {name: 'Ask about this lesson', exact: true}).inputValue(), followup)
  const completedRun = page.waitForResponse(async response => {
    if (response.request().method() !== 'GET' || response.url() !== base + '/api/v1/academy/projects/' + project.id + '/tutor-turns') return false
    const body = await response.json()
    return body.tutor_turns.length === 2 && body.tutor_turns[0].status === 'completed' && body.tutor_turns[0].require_execution
  })
  await openAcademySection(panel, 'tutor-options')
  await panel.getByRole('button', {name: 'Run and explain', exact: true}).click()
  const latest = (await (await completedRun).json()).tutor_turns[0]
  assert.equal(latest.require_execution, true)
  assert.ok(latest.actions.some(action => action.tool === 'run_example' && action.run_id), JSON.stringify(latest))
  assert.equal((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs.length, 2)
  await panel.locator('[data-tutor-turn-id="' + latest.id + '"]').getByLabel('Tutor response', {exact: true}).waitFor()
  assert.equal(laterPosts, 1)
  if (process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await page.getByRole('button', {name: 'Expand the right pane', exact: true}).click()
    await panel.getByRole('heading', {name: 'Learning assistant', exact: true}).scrollIntoViewIfNeeded()
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-native-tutor-en-dark-wide.png'})
  }
  assert.deepEqual(errors, [])
  console.log('Academy native tutor: save, real tools and sandbox output, idempotent recovery, IME, language/theme/width and hidden observation verified')
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
