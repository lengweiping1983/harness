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
    timer = setTimeout(() => reject(Error('Workbench fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Workbench fixture exited: ' + code + diagnostics)))
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
  const editor = panel.locator('.academy-workbench-main textarea[spellcheck="false"]')
  await editor.waitFor()
  let writes = 0
  page.on('request', request => {if (request.method() === 'POST' && request.url().includes('/academy/')) writes++})
  const source = await editor.inputValue() + '\n# workbench draft 🧪\n'
  const prompt = 'Keep my question / 保留问题 🧪'
  const note = 'My prediction / 保留预测'
  await openAcademySection(panel, 'quiz')
  await openAcademySection(panel, 'notes')
  const quiz = panel.locator('form').filter({has: page.locator('fieldset')}).first()
  await quiz.getByRole('radio').first().check()
  await quiz.getByRole('button', {name: '核对答案', exact: true}).click()
  await quiz.getByRole('button', {name: /查看提示 1/}).click()
  const selectedAnswer = await quiz.locator('input:checked').inputValue()
  await editor.fill(source)
  await panel.getByRole('textbox', {name: '向带练助手提问', exact: true}).fill(prompt)
  await panel.getByRole('textbox', {name: '学习说明', exact: true}).fill(note)
  await page.evaluate(() => {
    window.workbenchNodes = [...document.querySelectorAll('#inspector-tab-content textarea')]
  })
  const guide = panel.locator('details[tabindex="-1"]')
  assert.equal(await guide.getAttribute('open'), null)
  const focus = panel.getByRole('button', {name: '专注学习', exact: true})
  await focus.dispatchEvent('compositionstart')
  await focus.click()
  assert.equal(await focus.getAttribute('aria-pressed'), 'false')
  await focus.dispatchEvent('compositionend')
  await focus.click()
  assert.equal(await panel.getByRole('button', {name: '退出专注', exact: true}).getAttribute('aria-pressed'), 'true')
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    const guideButton = guide.locator(':scope > summary')
    for (const keyCode of [13, 229]) assert.equal(await guideButton.evaluate((button, keyCode) => !button.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode, isComposing: keyCode === 13, bubbles: true, cancelable: true})), keyCode), true)
    await guideButton.click()
    assert.notEqual(await guide.getAttribute('open'), null)
    assert.equal(await guideButton.evaluate(node => node === document.activeElement), true)
    await guideButton.click()
    for (const theme of ['light', 'dark']) for (const font of [10, 16, 22]) for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 1000})
      await page.waitForFunction(width => {const size = document.querySelector('.academy-workbench')?.getBoundingClientRect().width; return size && (width === 390 ? size <= 390 : size > 800)}, width)
      if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
      await page.evaluate(({theme, font}) => {document.documentElement.className = theme; document.documentElement.style.fontSize = font + 'px'}, {theme, font})
      const askAssistant = panel.getByRole('button', {name: copy['ui.academy.askAssistant'], exact: true})
      if (await askAssistant.isVisible()) {
        await askAssistant.click()
        assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).evaluate(node => node === document.activeElement), true)
      }
      const bounds = await page.evaluate(() => {
        const main = document.querySelector('.academy-workbench-main').getBoundingClientRect()
        const tutor = document.querySelector('.academy-workbench-tutor').getBoundingClientRect()
        const reflection = document.querySelector('.academy-workbench-reflection').getBoundingClientRect()
        const width = document.querySelector('.academy-workbench').getBoundingClientRect().width
        const threshold = 64 * parseFloat(getComputedStyle(document.documentElement).fontSize)
        const tutorNode = document.querySelector('.academy-workbench-tutor')
        const reflectionNode = document.querySelector('.academy-workbench-reflection')
        return {columns: width >= threshold, beside: tutor.left >= main.right && reflection.left === main.left && reflection.top >= main.bottom,
          stacked: tutor.top >= main.bottom && reflection.top >= tutor.bottom,
          tutorBeforeReflection: !!(tutorNode.compareDocumentPosition(reflectionNode) & Node.DOCUMENT_POSITION_FOLLOWING),
          overflow: document.documentElement.scrollWidth > innerWidth,
          sameNodes: window.workbenchNodes.every(node => node.isConnected && document.querySelector('#inspector-tab-content').contains(node))}
      })
      assert.equal(bounds.overflow, false, JSON.stringify({language, theme, font, width, bounds}))
      assert.equal(bounds.sameNodes, true, 'Layout and locale must retain editor instances')
      assert.equal(bounds.tutorBeforeReflection, true, 'Agent conversation precedes optional reflection in reading and keyboard order')
      assert.equal(bounds.columns ? bounds.beside : bounds.stacked, true, JSON.stringify({language, theme, font, width, bounds}))
      assert.equal(await quiz.locator('input:checked').inputValue(), selectedAnswer)
      assert.equal(await quiz.locator('ol > li').count(), 1)
      assert.equal(await quiz.locator('[role="status"]').count(), 1)
      assert.equal(await editor.inputValue(), source)
      assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).inputValue(), prompt)
      assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.note'], exact: true}).inputValue(), note)
      assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.note'], exact: true}).getAttribute('placeholder'), copy['ui.academy.noteHint'])
      assert.equal(await panel.locator('[data-academy-section="quiz"]').getByRole('heading').count(), 0)
      assert.equal(await panel.locator('[data-academy-section="notes"]').getByRole('heading').count(), 0)
    }
  }
  await page.evaluate(() => {document.documentElement.style.fontSize = '13px'})
  await page.setViewportSize({width: 1440, height: 1000})
  await page.waitForFunction(() => document.querySelector('.app-shell')?.getAttribute('data-mode') === 'columns')
  if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
  await panel.getByRole('button', {name: 'Exit focus', exact: true}).click()
  assert.equal(await panel.getByRole('button', {name: 'Focus on learning', exact: true}).getAttribute('aria-pressed'), 'false')
  assert.equal(await editor.inputValue(), source)
  assert.equal(writes, 0, 'Layout, locale, IME and navigation cannot write or invoke the Agent')
  if (process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await panel.getByRole('button', {name: 'Focus on learning', exact: true}).click()
    await editor.scrollIntoViewIfNeeded()
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-workbench-en-dark-wide.png'})
  }
  assert.deepEqual(errors, [])
  console.log('Academy workbench: focus, container layout, owned navigation, preserved drafts and DOM, IME, en/zh/en, theme/font/width matrix and no accidental writes verified')
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
