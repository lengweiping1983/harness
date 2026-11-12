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
    timer = setTimeout(() => reject(Error('Editor fixture startup timed out: ' + backendDiagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Editor fixture exited: ' + code + backendDiagnostics)))
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
  const code = initial + '\nprint("EDITOR_SHORTCUT_OUTPUT")\n'
  await editor.fill(code)
  let posts = 0
  page.on('request', request => {if (request.method() === 'POST' && /\/(run|check)$/.test(request.url())) posts++})
  const runButton = panel.getByRole('button', {name: '运行代码', exact: true})
  const group = panel.getByRole('group', {name: '运行与检查', exact: true})
  assert.equal(await runButton.getAttribute('aria-keyshortcuts'), 'Control+Enter Meta+Enter')
  const numbers = panel.locator('[data-academy-line-numbers]')
  assert.equal(await numbers.getAttribute('aria-hidden'), 'true')
  assert.equal(await numbers.locator('pre').textContent(), Array.from({length: code.split('\n').length}, (_, index) => index + 1).join('\n'))
  assert.equal(await editor.getAttribute('wrap'), 'off')
  const longLine = '# ' + '保持一行 / keep one line '.repeat(100)
  await editor.fill(code + longLine)
  assert.equal(await editor.evaluate(node => node.scrollWidth > node.clientWidth), true, 'Long code lines must scroll inside the editor')
  assert.equal(await numbers.locator('pre').textContent(), Array.from({length: code.split('\n').length}, (_, index) => index + 1).join('\n'))
  await editor.fill(code)
  await editor.press('Tab')
  assert.equal(await runButton.evaluate(node => node === document.activeElement), true, 'The code editor must not trap keyboard navigation')
  await runButton.press('Shift+Tab')
  assert.equal(await editor.evaluate(node => node === document.activeElement), true)
  await editor.evaluate(node => node.setSelectionRange(node.value.length, node.value.length))
  await editor.press('x')
  await editor.press(process.platform === 'darwin' ? 'Meta+z' : 'Control+z')
  assert.equal(await editor.inputValue(), code, 'The gutter must preserve native text undo')
  assert.equal(await group.evaluate(group => group.closest('section') === group.parentElement.querySelector('textarea')?.closest('section')), true)
  await editor.press('End')
  await editor.press('Enter')
  assert.equal(await editor.inputValue(), code + '\n', 'Plain Enter remains an editor newline')
  await editor.fill(code)
  for (const options of [{ctrlKey: true, isComposing: true}, {metaKey: true, keyCode: 229}, {ctrlKey: true, repeat: true}, {metaKey: true, shiftKey: true, repeat: true}]) {
    assert.equal(await editor.evaluate((node, options) => !node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true, ...options})), options), true)
  }
  await editor.press('Control+Alt+Enter')
  await editor.evaluate(node => {
    const event = new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true})
    Object.defineProperty(event, 'getModifierState', {value: key => key === 'AltGraph'})
    node.dispatchEvent(event)
  })
  await editor.dispatchEvent('compositionstart')
  await editor.press('Control+Enter')
  await editor.dispatchEvent('compositionend')
  await openAcademySection(panel, 'notes')
  await panel.getByRole('textbox', {name: '学习说明', exact: true}).fill('My prediction')
  await panel.getByRole('textbox', {name: '学习说明', exact: true}).press('Control+Enter')
  assert.equal(posts, 0)
  await editor.fill(code)
  const endpoint = '**/api/v1/academy/projects/' + project.id + '/run'
  await page.route(endpoint, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    assert.equal(route.request().postDataJSON().source.content, code)
    await route.fetch()
    await route.abort('failed')
  })
  const failed = page.waitForEvent('requestfailed', request => request.method() === 'POST' && request.url().endsWith('/run'))
  await editor.evaluate(node => {
    for (let index = 0; index < 2; index++) node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true}))
  })
  await failed
  assert.equal(posts, 1)
  assert.equal(await editor.isDisabled(), true)
  await editor.dispatchEvent('keydown', {key: 'Enter', ctrlKey: true, shiftKey: true, bubbles: true})
  assert.equal(posts, 1, 'An unconfirmed write blocks every execution shortcut')
  const pending = JSON.parse(await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1')))
  assert.equal(pending.executionInput.kind, 'run')
  assert.equal(pending.executionInput.source.content, code)
  await page.unroute(endpoint)
  await panel.getByRole('button', {name: '回查操作回执', exact: true}).click()
  await page.waitForFunction(() => !sessionStorage.getItem('harness.academy.pending.v1'))
  let runs = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs
  assert.equal(runs.length, 1)
  const executionDiagnostics = academyDiagnostics(browser)
  await executionDiagnostics.wait()
  const recovered = executionDiagnostics.snapshot().find(record => record.method === 'GET'
    && new URL(record.url).pathname === '/api/v1/academy/receipts/' + pending.requestId && record.state === 'received')
  assert.ok(recovered, 'Lost execution responses must retain the actual receipt recovery body')
  assert.deepEqual(JSON.parse(recovered.body).result.run.result, runs[0].result)
  assert.ok(runs[0].result.stdout.includes('EDITOR_SHORTCUT_OUTPUT'))
  await panel.locator('[data-academy-run-id="' + runs[0].id + '"]').waitFor()
  const order = await panel.evaluate(node => {
    const editor = node.querySelector('textarea[spellcheck="false"]')
    const controls = editor.closest('section').querySelector('[role="group"]')
    const output = node.querySelector('[data-academy-run-id]')
    const quiz = node.querySelector('fieldset')
    return {sameSection: controls.closest('section').contains(output), controlsBeforeOutput: Boolean(controls.compareDocumentPosition(output) & Node.DOCUMENT_POSITION_FOLLOWING), outputBeforeQuiz: Boolean(output.compareDocumentPosition(quiz) & Node.DOCUMENT_POSITION_FOLLOWING)}
  })
  assert.deepEqual(order, {sameSection: true, controlsBeforeOutput: true, outputBeforeQuiz: true})
  const executeShortcut = async key => {
    await editor.evaluate(node => {
      node.focus(); node.setSelectionRange(4, 11, 'backward'); node.scrollTop = 35
      node.addEventListener('keydown', () => {window.expectedEditorSelection = {start: node.selectionStart, end: node.selectionEnd, direction: node.selectionDirection, top: node.scrollTop, left: node.scrollLeft}}, {once: true})
    })
    await page.waitForFunction(() => document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
    const completed = page.waitForResponse(response => response.request().method() === 'POST' && /\/(run|check)$/.test(response.url()))
    await editor.press(key)
    const receipt = await (await completed).json()
    assert.equal(receipt.status, 'ready')
    await panel.locator('[data-academy-run-id="' + receipt.result.run.id + '"]').waitFor()
    await page.waitForFunction(() => document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
    assert.equal(await editor.evaluate(node => node === document.activeElement), true, 'A keyboard execution should return focus to its editor after the confirmed refresh')
    const selection = await editor.evaluate(node => ({actual: {start: node.selectionStart, end: node.selectionEnd, direction: node.selectionDirection, top: node.scrollTop, left: node.scrollLeft}, expected: window.expectedEditorSelection}))
    assert.deepEqual(selection.actual, selection.expected, 'Unchanged code preserves the selection and editor scroll position')
    await page.waitForFunction(() => {
      const node = document.querySelector('textarea[aria-label="练习代码"]')
      const pre = document.querySelector('[data-academy-line-numbers] pre')
      return node && pre && new DOMMatrix(getComputedStyle(pre).transform).m42 === -node.scrollTop
    })
    return receipt.result.run
  }
  await editor.fill(code + Array.from({length: 80}, (_, index) => '# editor scroll ' + index + '\n').join(''))
  assert.equal((await executeShortcut('Meta+Enter')).kind, 'run')
  assert.equal((await executeShortcut('Control+Shift+Enter')).kind, 'check')
  assert.equal(posts, 3)
  const codeBefore = await editor.inputValue()
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    for (const theme of ['light', 'dark']) for (const font of [10, 22]) for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 1000})
      await page.waitForFunction(width => document.querySelector('.app-shell')?.getAttribute('data-mode') === (width === 390 ? 'overlay' : 'columns'), width)
      if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
      await page.evaluate(({theme, font}) => {document.documentElement.className = theme; document.documentElement.style.fontSize = font + 'px'}, {theme, font})
      const source = panel.getByRole('textbox', {name: copy['ui.academy.source'], exact: true})
      assert.equal(await source.inputValue(), codeBefore)
      assert.equal(await numbers.locator('pre').textContent(), Array.from({length: codeBefore.split('\n').length}, (_, index) => index + 1).join('\n'))
      const metrics = await source.evaluate(node => {
        const pre = node.parentElement.querySelector('[data-academy-line-numbers] pre')
        const code = getComputedStyle(node), gutter = getComputedStyle(pre)
        return {codeFont: code.fontFamily, gutterFont: gutter.fontFamily, codeSize: code.fontSize, gutterSize: gutter.fontSize,
          codeLine: code.lineHeight, gutterLine: gutter.lineHeight, wrap: code.whiteSpace}
      })
      assert.equal(metrics.codeFont, metrics.gutterFont)
      assert.equal(metrics.codeSize, metrics.gutterSize)
      assert.equal(metrics.codeLine, metrics.gutterLine)
      assert.equal(metrics.wrap, 'pre')
      const run = panel.getByRole('button', {name: copy['ui.academy.run'], exact: true})
      assert.equal(await run.getAttribute('aria-keyshortcuts'), 'Control+Enter Meta+Enter')
      assert.equal(await panel.getByRole('button', {name: copy['ui.academy.check'], exact: true}).getAttribute('aria-keyshortcuts'), 'Control+Shift+Enter Meta+Shift+Enter')
      await openAcademySection(panel, 'code-options')
      const limits = panel.locator('details').filter({has: page.locator(':scope > summary', {hasText: copy['ui.academy.executionLimits']})})
      await limits.locator(':scope > summary').click()
      await limits.getByText(copy['ui.academy.limits'], {exact: true}).waitFor()
      await limits.locator(':scope > summary').click()
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    }
  }
  assert.equal(posts, 3)
  runs = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs
  assert.deepEqual(runs.map(run => run.kind), ['check', 'run', 'run'])
  if (process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await page.evaluate(() => {document.documentElement.style.fontSize = '13px'})
    await panel.getByRole('button', {name: 'Focus on learning', exact: true}).click()
    await panel.getByRole('textbox', {name: 'Practice code', exact: true}).scrollIntoViewIfNeeded()
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-code-editor-en-dark-wide.png'})
  }
  assert.deepEqual(errors, [])
  console.log('Academy editor: adjacent source/actions/results, real Ctrl/Meta run and Shift check, atomic save, unknown receipt recovery, IME/repeat/Alt guards, scoped shortcuts and en/zh/en theme/font/width verified')
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
