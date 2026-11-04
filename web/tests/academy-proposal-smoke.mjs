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
    timer = setTimeout(() => reject(Error('Proposal fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Proposal fixture exited: ' + code + diagnostics)))
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
  const before = 'value = 1\nprint(value)\n'
  const after = 'value = 2\nprint(value)\n'
  await editor.fill(before)
  const question = 'Keep my own question / 保留原问题'
  await openAcademySection(panel, 'tutor-options')
  await panel.getByRole('textbox', {name: '向带练助手提问', exact: true}).fill(question)
  let posts = 0
  const endpoint = '**/api/v1/academy/projects/' + project.id + '/tutor-turns'
  await page.route(endpoint, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    posts++
    const request = route.request().postDataJSON()
    assert.equal(request.proposal_path, 'main.py')
    assert.equal(request.allow_execution, false)
    assert.equal(request.require_execution, false)
    assert.equal(request.source.content, before)
    const response = await route.fetch()
    const body = await response.json()
    body.result.tutor_turn.proposal_path = 'wrong.py'
    await route.fulfill({response, json: body})
  })
  const suggest = panel.getByRole('button', {name: '保存并建议小改动', exact: true})
  await suggest.dispatchEvent('compositionstart')
  await suggest.click()
  assert.equal(posts, 0)
  await suggest.dispatchEvent('compositionend')
  for (const keyCode of [13, 229]) assert.equal(await suggest.evaluate((button, keyCode) => !button.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode, isComposing: keyCode === 13, bubbles: true, cancelable: true})), keyCode), true)
  const completedProposal = page.waitForResponse(async response => {
    if (response.request().method() !== 'GET' || !response.url().endsWith('/tutor-turns')) return false
    return (await response.json()).tutor_turns?.some(turn => turn.proposal_path === 'main.py' && turn.status === 'completed')
  })
  await suggest.evaluate(button => {button.click(); button.click()})
  await panel.getByRole('alert').waitFor()
  assert.equal(posts, 1)
  const pending = JSON.parse(await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1')))
  assert.equal(pending.tutorInput.proposal_path, 'main.py')
  await page.unroute(endpoint)
  await panel.getByRole('button', {name: '回查操作回执', exact: true}).click()
  await page.waitForFunction(() => !sessionStorage.getItem('harness.academy.pending.v1'))
  const proposalRegion = panel.getByRole('region', {name: '代码修改建议', exact: true})
  await proposalRegion.locator('.diff-view').waitFor()
  const tryEdit = proposalRegion.getByRole('button', {name: '放进实验台试试', exact: true})
  await tryEdit.waitFor({state: 'visible'})
  const turns = (await (await completedProposal).json()).tutor_turns
  assert.equal(turns.length, 1)
  assert.equal(turns[0].status, 'completed')
  const proposal = turns[0].actions.find(action => action.proposal).proposal
  assert.equal(proposal.before, before)
  assert.equal(proposal.after, after)
  assert.equal(await editor.inputValue(), before)
  const sourceURL = base + '/api/v1/academy/projects/' + project.id + '/source?path=main.py'
  assert.equal((await (await page.request.get(sourceURL)).json()).content, before)
  assert.equal((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs.length, 0)
  await tryEdit.dispatchEvent('compositionstart')
  await tryEdit.click()
  assert.equal(await editor.inputValue(), before)
  await tryEdit.dispatchEvent('compositionend')
  // 另一窗口已留下的目标文件草稿必须保留。
  const draftKey = 'harness.academy.draft.v1:' + JSON.stringify([project.id, 'main.py'])
  const otherDraft = JSON.stringify({content: 'value = 99\n', version: proposal.expected_version})
  await page.evaluate(({draftKey, otherDraft}) => localStorage.setItem(draftKey, otherDraft), {draftKey, otherDraft})
  await tryEdit.click()
  assert.equal(await page.evaluate(key => localStorage.getItem(key), draftKey), otherDraft)
  assert.equal(await editor.inputValue(), before)
  await page.evaluate(key => localStorage.removeItem(key), draftKey)
  await tryEdit.click()
  assert.equal(await editor.inputValue(), after)
  assert.equal((await (await page.request.get(sourceURL)).json()).content, before, 'Trying the proposal only creates an editable draft')
  assert.equal((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs.length, 0)
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
      await panel.getByRole('region', {name: copy['ui.academy.tutor.proposal'], exact: true}).locator('.diff-view').waitFor()
      assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.source'], exact: true}).inputValue(), after)
      assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).inputValue(), question)
      assert.equal(await panel.getByRole('button', {name: copy['ui.academy.tutor.tryProposal'], exact: true}).isDisabled(), true)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    }
  }
  const executed = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/run'))
  await panel.getByRole('button', {name: 'Run code', exact: true}).click()
  assert.equal((await executed).status(), 200)
  await panel.getByRole('button', {name: 'Help me understand this result', exact: true}).waitFor()
  const runs = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs
  assert.equal(runs.length, 1)
  assert.equal(runs[0].result.stdout.trim(), '2')
  assert.equal((await (await page.request.get(sourceURL)).json()).content, after)
  assert.equal(await panel.getByRole('button', {name: 'Try in the editor', exact: true}).isDisabled(), true, 'A saved edit cannot reuse the proposal against its earlier fingerprint')
  if (process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await panel.getByRole('button', {name: 'Focus on learning', exact: true}).click()
    await panel.getByRole('region', {name: 'Suggested code change', exact: true}).scrollIntoViewIfNeeded()
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-proposed-edit-en-dark-wide.png'})
  }
  assert.equal(posts, 1)
  assert.deepEqual(errors, [])
  console.log('Academy proposals: native inspect/propose loop, real diff, draft-only trial, user-controlled save/run, stale and other-window draft protection, IME, receipt identity, en/zh/en, themes and responsive layout verified')
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
