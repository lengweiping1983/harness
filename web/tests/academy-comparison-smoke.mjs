import {requireAcademySandbox} from './academy-environment.mjs'
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
    timer = setTimeout(() => reject(Error('Comparison fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Comparison fixture exited: ' + code + diagnostics)))
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
  let writes = 0
  page.on('request', request => {if (request.method() === 'POST' && request.url().includes('/academy/')) writes++})
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    const comparison = panel.locator('details').filter({has: page.locator(':scope > summary', {hasText: copy['ui.academy.compare.title']})})
    const select = comparison.getByRole('combobox', {name: copy['ui.academy.compare.choose'], exact: true})
    assert.equal(await select.inputValue(), first.id, 'Language changes preserve the selected earlier record')
    for (const theme of ['light', 'dark']) for (const font of [10, 22]) for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 1000})
      await page.waitForFunction(width => document.querySelector('.app-shell')?.getAttribute('data-mode') === (width === 390 ? 'overlay' : 'columns'), width)
      if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
      await page.evaluate(({theme, font}) => {document.documentElement.className = theme; document.documentElement.style.fontSize = font + 'px'}, {theme, font})
      if (await comparison.getAttribute('open') === null) await comparison.locator(':scope > summary').click()
      await select.selectOption(first.id)
      const older = comparison.getByRole('region', {name: copy['ui.academy.compare.before'], exact: true})
      const newer = comparison.getByRole('region', {name: copy['ui.academy.compare.after'], exact: true})
      assert.equal(await older.locator('pre').textContent(), first.result.stdout)
      assert.equal(await newer.locator('pre').textContent(), third.result.stdout)
      await comparison.getByText(copy['ui.academy.compare.changedOutput'], {exact: true}).waitFor()
      const bounds = await comparison.evaluate(node => {
        const container = node.querySelector('.academy-run-comparison'), cards = [...node.querySelectorAll('.academy-run-comparison-grid > section')].map(child => child.getBoundingClientRect())
        const columns = container.getBoundingClientRect().width >= 40 * parseFloat(getComputedStyle(document.documentElement).fontSize)
        return {columns, beside: cards[1].left >= cards[0].right, stacked: cards[1].top >= cards[0].bottom, overflow: document.documentElement.scrollWidth > innerWidth}
      })
      assert.equal(bounds.overflow, false)
      assert.equal(bounds.columns ? bounds.beside : bounds.stacked, true, JSON.stringify(bounds))
    }
  }
  assert.equal(writes, 0, 'Reading and selecting comparisons cannot execute code or ask the model')
  assert.equal((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs.length, 4)
  if (process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await page.evaluate(() => {document.documentElement.style.fontSize = '13px'})
    await panel.getByRole('button', {name: 'Focus on learning', exact: true}).click()
    const comparison = panel.locator('details').filter({has: page.locator(':scope > summary', {hasText: 'Compare with an earlier run'})})
    if (await comparison.getAttribute('open') === null) await comparison.locator(':scope > summary').click()
    await comparison.getByRole('combobox', {name: 'Compare against', exact: true}).selectOption(first.id)
    await comparison.scrollIntoViewIfNeeded()
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-run-comparison-en-dark-wide.png'})
  }
  assert.deepEqual(errors, [])
  console.log('Academy comparisons: real before/after runs, identical output/code, separate checker evidence, selected metadata and scope, no writes, en/zh/en selection and theme/font/width layout verified')
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
