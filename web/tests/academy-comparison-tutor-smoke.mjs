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
    timer = setTimeout(() => reject(Error('Comparison tutor fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Comparison tutor fixture exited: ' + code + diagnostics)))
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
  const execute = async (value, check = false) => {
    if (value !== undefined) await editor.fill('value = ' + value + '\nprint(value)\n')
    const completed = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(check ? '/check' : '/run'))
    await panel.getByRole('button', {name: check ? '检查练习' : value === undefined ? '运行代码' : '运行代码', exact: true}).click()
    const response = await completed
    assert.equal(response.status(), 200)
    const receipt = await response.json()
    assert.equal(receipt.status, 'ready')
    await panel.locator('[data-academy-run-id="' + receipt.result.run.id + '"]').waitFor()
    return receipt.result.run
  }
  const first = await execute(1)
  assert.equal(await panel.locator('summary').filter({hasText: '对照之前的运行'}).count(), 0)
  const second = await execute(2)
  const disclosure = panel.locator('details').filter({has: page.locator(':scope > summary', {hasText: '对照之前的运行'})})
  await disclosure.locator(':scope > summary').click()
  assert.ok(await disclosure.getByText('记录到的输出发生了变化。', {exact: true}).isVisible())
  const previous = disclosure.getByRole('region', {name: '之前的运行', exact: true})
  const current = disclosure.getByRole('region', {name: '最新运行', exact: true})
  assert.equal(await previous.locator('pre').textContent(), first.result.stdout)
  assert.equal(await current.locator('pre').textContent(), second.result.stdout)
  assert.notEqual(first.input_fingerprint, second.input_fingerprint)
  const checked = await execute(undefined, true)
  assert.equal(checked.kind, 'check')
  assert.equal(await panel.locator('summary').filter({hasText: '对照之前的运行'}).count(), 0, 'A checker cannot compare itself with an example execution')
  const third = await execute(undefined)
  assert.equal(third.input_fingerprint, second.input_fingerprint)
  await disclosure.locator(':scope > summary').click()
  await disclosure.getByText('记录到的输出相同。', {exact: true}).waitFor()
  const choose = disclosure.getByRole('combobox', {name: '选择对照记录', exact: true})
  assert.deepEqual(await choose.locator('option').evaluateAll(nodes => nodes.map(node => node.value)), [second.id, first.id])
  await choose.selectOption(first.id)
  await disclosure.getByText('记录到的输出发生了变化。', {exact: true}).waitFor()
  await previous.locator('summary').click()
  await previous.getByText(first.id, {exact: true}).waitFor()
  await current.locator('summary').click()
  await current.getByText(third.id, {exact: true}).waitFor()
  const ownQuestion = 'My own question / 不要覆盖草稿'
  await panel.getByRole('textbox', {name: '向带练助手提问', exact: true}).fill(ownQuestion)
  const ask = disclosure.getByRole('button', {name: '和智能体一起分析变化', exact: true})
  await editor.fill('value = 3\nprint(value)\n')
  assert.equal(await ask.isDisabled(), true)
  await panel.getByRole('button', {name: '放弃未保存修改', exact: true}).click()
  let posts = 0
  const endpoint = '**/api/v1/academy/projects/' + project.id + '/tutor-turns'
  await page.route(endpoint, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    posts++
    const body = route.request().postDataJSON()
    assert.equal(body.evidence_run_id, third.id)
    assert.equal(body.comparison_run_id, first.id)
    assert.equal(body.source, undefined)
    assert.equal(body.allow_execution, false)
    assert.equal(body.require_execution, false)
    const response = await route.fetch()
    const receipt = await response.json()
    receipt.result.tutor_turn.comparison_run_id = second.id
    await route.fulfill({response, json: receipt})
  })
  await ask.dispatchEvent('compositionstart')
  await ask.click()
  assert.equal(posts, 0)
  await ask.dispatchEvent('compositionend')
  for (const keyCode of [13, 229]) assert.equal(await ask.evaluate((button, keyCode) => !button.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode, isComposing: keyCode === 13, bubbles: true, cancelable: true})), keyCode), true)
  const completed = page.waitForResponse(async response => {
    if (response.request().method() !== 'GET' || !response.url().endsWith('/tutor-turns')) return false
    return (await response.json()).tutor_turns?.some(turn => turn.evidence_run_id === third.id && turn.comparison_run_id === first.id && turn.status === 'completed')
  })
  await ask.evaluate(button => {button.click(); button.click()})
  await choose.selectOption(second.id)
  await panel.getByRole('alert').waitFor()
  assert.equal(posts, 1)
  const pending = JSON.parse(await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1')))
  assert.equal(pending.tutorInput.evidence_run_id, third.id)
  assert.equal(pending.tutorInput.comparison_run_id, first.id)
  await page.unroute(endpoint)
  await panel.getByRole('button', {name: '回查操作回执', exact: true}).click()
  await page.waitForFunction(() => !sessionStorage.getItem('harness.academy.pending.v1'))
  const turn = (await (await completed).json()).tutor_turns[0]
  assert.equal(turn.evidence_run_id, third.id)
  assert.equal(turn.comparison_run_id, first.id)
  assert.deepEqual(turn.actions, [])
  assert.equal(turn.allow_execution, false)
  assert.equal(turn.require_execution, false)
  const answer = panel.getByLabel('带练回复', {exact: true})
  await answer.locator('.md').waitFor()
  assert.deepEqual(await answer.locator('pre code').allTextContents(), [first.result.stdout.trimEnd(), third.result.stdout.trimEnd()])
  assert.equal(await choose.inputValue(), second.id, 'A completed question cannot rewrite the current baseline selection')
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    await openAcademySection(panel, 'tutor-evidence')
    await panel.getByText(copy['ui.academy.tutor.resultEvidence'].replace('{id}', third.id), {exact: true}).waitFor()
    await panel.getByText(copy['ui.academy.tutor.comparisonEvidence'].replace('{id}', first.id), {exact: true}).waitFor()
    assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).inputValue(), ownQuestion)
    await panel.getByLabel(copy['ui.academy.tutor.answer'], {exact: true}).locator('.md').waitFor()
    assert.deepEqual(await panel.getByLabel(copy['ui.academy.tutor.answer'], {exact: true}).locator('pre code').allTextContents(), [first.result.stdout.trimEnd(), third.result.stdout.trimEnd()])
  }
  assert.equal((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs.length, 4)
  assert.equal(posts, 1)
  if (process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await panel.getByRole('button', {name: 'Focus on learning', exact: true}).click()
    await panel.getByLabel('Tutor response', {exact: true}).scrollIntoViewIfNeeded()
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-comparison-tutor-en-dark-wide.png'})
  }
  assert.deepEqual(errors, [])
  console.log('Academy comparison tutoring: exact selected pair, real sandbox outputs, changed baseline isolation, read-only native loop, mismatched receipt recovery, draft/IME guards and en/zh/en evidence verified')
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
