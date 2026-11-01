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
    timer = setTimeout(() => reject(Error('Simple learning fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Simple learning fixture exited: ' + code + diagnostics)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (Number.isInteger(value.port)) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  const base = 'http://127.0.0.1:' + ready.port
  browser = await launchBrowser()
  page = await browser.newPage({viewport: {width: 1440, height: 1000}})
  const errors = []
  let designReads = 0
  page.on('request', request => {if (request.method() === 'GET' && request.url().endsWith('/academy/designs')) designReads++})
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(base)
  await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  await page.keyboard.press('Control+j')
  const helpGroup=page.locator('#inspector-tab-content[data-panel="home"] details').filter({hasText:'学习与帮助'})
  if ((await helpGroup.getAttribute('open')) === null) await helpGroup.locator('summary').click()
  await page.locator('#inspector-tab-content[data-panel="home"]').getByRole('button', {name: '学习中心', exact: true}).click()
  const panel = page.locator('#inspector-tab-content[data-panel="academy"]')
  await panel.locator('[data-course-id="harness-foundations"]').getByRole('button').waitFor()
  assert.equal(await panel.getByRole('combobox', {name: '选择一门课程', exact: true}).isVisible(), false)
  assert.equal(await panel.getByRole('button', {name: '创建项目', exact: true}).isVisible(), false)
  assert.equal(await panel.getByRole('textbox', {name: '项目名称', exact: true}).isVisible(), false)
  assert.equal(await panel.getByRole('heading', {name: /第 1 课/}).count(), 0)
  const topicSection = panel.locator('[data-academy-section="topic"]')
  assert.equal(await topicSection.getAttribute('open'), null)
  assert.equal(await panel.getByRole('textbox', {name: '你想学习什么？', exact: true}).count(), 0)
  assert.equal(designReads, 0, 'The initial learning page must not observe unused course designs')
  if (process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-entry.png'})
  }
  const topicDraft = '用一个生活例子理解智能体'
  const initialDesigns = page.waitForResponse(response => response.request().method() === 'GET' && response.url().endsWith('/academy/designs'))
  await openAcademySection(panel, 'topic')
  await initialDesigns
  await panel.getByRole('textbox', {name: '你想学习什么？', exact: true}).fill(topicDraft)
  let releaseDesigns, sawDesigns, handledDesigns
  const designGate = new Promise(resolve => {releaseDesigns = resolve})
  const designObserved = new Promise(resolve => {sawDesigns = resolve})
  const designHandled = new Promise(resolve => {handledDesigns = resolve})
  releases.push(releaseDesigns)
  await page.route('**/api/v1/academy/designs', async route => {
    assert.equal(route.request().method(), 'GET')
    const response = await route.fetch()
    sawDesigns()
    await designGate
    try {await route.fulfill({response})} catch (error) {if (!route.request().failure()) throw error} finally {handledDesigns()}
  })
  const designAborted = page.waitForEvent('requestfailed', {predicate: request => request.method() === 'GET' && request.url().endsWith('/academy/designs')})
  await panel.getByRole('button', {name: '刷新我的制课记录', exact: true}).click()
  await designObserved
  await topicSection.locator(':scope > summary').click()
  await designAborted
  releaseDesigns()
  await designHandled
  await page.unroute('**/api/v1/academy/designs')
  assert.equal(await panel.getByRole('textbox', {name: '你想学习什么？', exact: true}).count(), 0)
  await openAcademySection(panel, 'topic')
  assert.equal(await panel.getByRole('textbox', {name: '你想学习什么？', exact: true}).inputValue(), topicDraft)
  await topicSection.locator(':scope > summary').click()
  await panel.locator('[data-course-id="harness-foundations"]').getByRole('button').click()
  const editor = panel.getByRole('textbox', {name: '练习代码', exact: true})
  await editor.waitFor()
  const task = panel.locator('[data-academy-task]')
  assert.equal(await task.isVisible(), true, 'The learner must see what to change without opening the guide')
  assert.deepEqual(await task.locator('code').allTextContents(), ['question', 'reply'])
  const assistant = panel.getByRole('complementary', {name: '学习助手', exact: true})
  assert.equal(await assistant.locator('button:visible').count(), 1)
  assert.equal(await panel.getByRole('combobox', {name: '课程目录', exact: true}).isVisible(), false)
  assert.equal(await panel.getByRole('button', {name: '下一课', exact: true}).isVisible(), false)
  assert.equal(await panel.getByRole('radio').first().isVisible(), false)
  assert.equal(await panel.getByRole('textbox', {name: '学习说明', exact: true}).isVisible(), false)
  assert.equal(await panel.getByRole('button', {name: '提交并获取点评', exact: true}).isVisible(), false)
  assert.equal(await panel.getByRole('button', {name: '保存修改', exact: true}).isVisible(), false)
  assert.equal(await assistant.getByRole('checkbox').isVisible(), false)
  for (const section of ['quiz', 'notes', 'feedback', 'code-options', 'tutor-options']) assert.equal(await panel.locator('[data-academy-section="' + section + '"]').getAttribute('open'), null)
  const visible = await panel.innerText()
  assert.equal(visible.includes('每个维度独立观察'), false)
  assert.equal(visible.includes('智能体做了什么'), false)
  assert.equal(visible.includes('宿主执行事实'), false)
  const source = await editor.inputValue()
  const ownQuestion = '这两行代码是怎么得到输出的？'
  await assistant.getByRole('textbox', {name: '向带练助手提问', exact: true}).fill(ownQuestion)
  await panel.getByRole('button', {name: '专注学习', exact: true}).click()
  const sourceNode = await editor.elementHandle()
  const questionNode = await assistant.getByRole('textbox', {name: '向带练助手提问', exact: true}).elementHandle()
  let writes = 0
  page.on('request', request => {if (request.method() === 'POST' && request.url().includes('/academy/')) writes++})
  const manifest = JSON.parse(await readFile(new URL('../../harness/academy/content/harness-foundations/course.json', import.meta.url), 'utf-8'))
  const goals = new Map()
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    const translated = page.waitForResponse(response => response.request().method() === 'GET' && response.url().endsWith('/academy/courses/harness-foundations'))
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    await page.waitForFunction(() => document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
    const course = (await (await translated).json()).course
    assert.equal(await task.isVisible(), true)
    await page.waitForFunction(expected => document.querySelector('[data-academy-task]')?.textContent === expected, course.lessons[0].exercise.replaceAll('`', ''))
    assert.equal(await task.count(), 1, 'The task belongs beside the editor, without a second copy inside the folded guide')
    assert.equal(await panel.locator('p').filter({hasText: course.lessons[0].exercise.replaceAll('`', '')}).count(), 1)
    const resources = JSON.parse(await readFile(new URL('../../harness/academy/content/harness-foundations/locales/' + language + '.json', import.meta.url), 'utf-8'))
    assert.equal(course.lessons[0].goal, resources[manifest.lessons[0].goal_key], 'The requested language must reach the actual lesson consumer')
    await panel.getByText(course.lessons[0].goal, {exact: true}).waitFor()
    if (goals.has(language)) assert.equal(goals.get(language), course.lessons[0].goal)
    goals.set(language, course.lessons[0].goal)
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    assert.equal(await panel.locator('[data-academy-section="catalog"] > summary').textContent(), copy['ui.academy.courseSettings'])
    for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 1000})
      await page.waitForFunction(width => {const size = document.querySelector('.academy-workbench')?.getBoundingClientRect().width; return size && (width === 390 ? size <= 390 : size > 800)}, width)
      await page.evaluate(theme => document.documentElement.className = theme, theme)
      const tutor = panel.getByRole('complementary', {name: copy['ui.academy.tutor.title'], exact: true})
      assert.equal(await tutor.locator('button:visible').count(), 1)
      assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.source'], exact: true}).inputValue(), source)
      assert.equal(await tutor.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).inputValue(), ownQuestion)
      assert.equal(await sourceNode.evaluate(node => node.isConnected), true)
      assert.equal(await questionNode.evaluate(node => node.isConnected), true)
      const jump = panel.getByRole('button', {name: copy['ui.academy.askAssistant'], exact: true})
      if (await jump.isVisible()) {
        await jump.click()
        assert.equal(await tutor.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).evaluate(node => node === document.activeElement), true)
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      if (process.env.HARNESS_TEST_SCREENSHOTS) {
        await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
        await tutor.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).fill('')
        await panel.evaluate(node => node.scrollIntoView({block: 'start'}))
        await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-simple-' + language + '-' + theme + '-' + width + '.png'})
        await tutor.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).fill(ownQuestion)
      }
    }
  }
  assert.notEqual(goals.get('en'), goals.get('zh-CN'), 'Lesson content must actually follow the language switch')
  const options = panel.locator('[data-academy-section="tutor-options"]')
  await options.locator(':scope > summary').click()
  await panel.getByRole('checkbox', {name: 'Allow this lesson\'s code to run', exact: true}).check()
  await options.locator(':scope > summary').click()
  await panel.getByText('The assistant may run this lesson’s code.', {exact: true}).waitFor()
  await panel.getByRole('button', {name: 'Turn off', exact: true}).click()
  assert.equal(await panel.getByText('The assistant may run this lesson’s code.', {exact: true}).count(), 0)
  const feedback = panel.locator('[data-academy-section="feedback"]')
  let entered, release, served
  const finished = new Promise(resolve => {served = resolve})
  const requested = new Promise(resolve => {entered = resolve})
  const endpoint = '**/api/v1/academy/projects/*/submissions'
  await page.route(endpoint, async route => {
    const response = await route.fetch()
    entered()
    await new Promise(resolve => {release = resolve; releases.push(resolve)})
    await route.fulfill({response})
    served()
  })
  await feedback.locator(':scope > summary').click()
  await requested
  const aborted = page.waitForEvent('requestfailed', request => request.method() === 'GET' && request.url().endsWith('/submissions'))
  await feedback.locator(':scope > summary').click()
  await aborted
  release()
  await finished
  await page.unroute(endpoint)
  assert.equal(writes, 0, 'Disclosures, permission choices and reading feedback cannot write or ask the model')
  const completedAnswer = page.waitForResponse(async response => {
    if (response.request().method() !== 'GET' || !response.url().endsWith('/tutor-turns')) return false
    return (await response.json()).tutor_turns?.some(turn => turn.status === 'completed' && turn.prompt === ownQuestion)
  })
  const question = panel.getByRole('textbox', {name: 'Ask about this lesson', exact: true})
  await question.press('Shift+Enter')
  assert.equal(await question.inputValue(), ownQuestion + '\n')
  await question.fill(ownQuestion)
  for (const keyCode of [13, 229]) assert.equal(await question.evaluate((node, keyCode) => !node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode, isComposing: keyCode === 13, bubbles: true, cancelable: true})), keyCode), true)
  assert.equal(writes, 0)
  await question.press('Enter')
  await completedAnswer
  await panel.getByLabel('Tutor response', {exact: true}).locator('.md').waitFor()
  assert.equal(await panel.locator('[data-academy-section="tutor-evidence"]').getAttribute('open'), null)
  assert.equal(await panel.getByRole('textbox', {name: 'Ask about this lesson', exact: true}).inputValue(), ownQuestion)
  assert.equal(writes, 1)

  assert.deepEqual(errors, [])
  console.log('Simple Academy: minimal default controls, progressive disclosure, persistent code/question, visible execution permission, paused hidden feedback and en/zh/en responsive themes verified')
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
