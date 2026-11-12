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
    timer = setTimeout(() => reject(Error('Model flow fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Model flow fixture exited: ' + code + diagnostics)))
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
  const helpGroup = page.locator('#inspector-tab-content[data-panel="home"] details').filter({hasText: '学习与帮助'})
  if ((await helpGroup.getAttribute('open')) === null) await helpGroup.locator('summary').click()
  await page.locator('#inspector-tab-content[data-panel="home"]').getByRole('button', {name: '学习中心', exact: true}).click()
  const panel = page.locator('#inspector-tab-content[data-panel="academy"]')
  await panel.locator('[data-course-id="llm-foundations"]').getByRole('button').click()
  await panel.locator('[data-academy-study-action]').waitFor()
  const readingNote=panel.locator('[data-academy-section="notes"] textarea')
  const readingDraft='My own example / 我自己的例子'
  await readingNote.fill(readingDraft)
  for(const language of ['en','zh-CN','en','zh-CN']){
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language=>document.documentElement.lang===language&&!document.getElementById('language-preference').disabled,language)
    await page.keyboard.press('Control+,')
    const strings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url),'utf-8'))
    assert.equal(await readingNote.getAttribute('placeholder'),strings['ui.academy.studyNoteHint'])
    assert.equal(await readingNote.getAttribute('aria-label'),strings['ui.academy.note'])
    assert.equal(await readingNote.inputValue(),readingDraft)
    assert.equal(await panel.locator('[data-academy-section="quiz"]').getByRole('heading').count(),0)
    assert.equal(await panel.locator('[data-academy-section="notes"]').getByRole('heading').count(),0)
    assert.equal(await panel.getByRole('heading',{name:strings['ui.academy.studyPractice'],exact:true}).count(),0)
  }
  assert.equal(await panel.locator('.academy-workbench-main').count(), 0, 'A reading lesson must not render an empty experiment column')
  assert.equal(await panel.locator('.academy-workbench-tutor').evaluate(node => !!(node.compareDocumentPosition(document.querySelector('[data-academy-section="quiz"]')) & Node.DOCUMENT_POSITION_FOLLOWING)), true)
  const get = async path => (await page.request.get(base + '/api/v1/academy' + path)).json()
  const course = (await get('/courses/llm-foundations')).course
  const lesson = course.lessons.find(lesson => lesson.mode === 'model')
  const project = (await get('/projects')).projects[0]
  await openAcademySection(panel, 'manage')
  await panel.getByRole('combobox', {name: '课程目录', exact: true}).selectOption(lesson.id)
  await panel.getByRole('button', {name: '保存当前版本并打开本课', exact: true}).click()
  let prompt = panel.getByRole('textbox', {name: '实验提示词', exact: true})
  await prompt.waitFor()
  const guide = panel.locator('summary').filter({hasText: '看讲解'}).locator('..')
  assert.equal(await guide.getAttribute('open'), null)
  assert.equal(await panel.locator('[data-academy-task]').textContent(), lesson.exercise)
  for (const section of ['quiz', 'notes']) assert.equal(await panel.locator('[data-academy-section="' + section + '"]').getAttribute('open'), null)
  assert.equal(await panel.getByRole('button', {name: '完成本课学习活动', exact: true}).count(), 0)
  const initial = await prompt.inputValue() + '\nOwn input / 用户输入保持原文。'
  await prompt.fill(initial)
  const node = await prompt.elementHandle()
  let modelWrites = 0, completions = 0
  page.on('request', request => {
    if (request.method() !== 'POST') return
    if (request.url().endsWith('/model-runs')) modelWrites++
    if (request.url().endsWith('/complete')) completions++
  })
  let copy
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    prompt = panel.getByRole('textbox', {name: copy['ui.academy.modelPrompt'], exact: true})
    await prompt.waitFor()
    assert.equal(await panel.locator('[data-academy-section="notes"] textarea').getAttribute('placeholder'),copy['ui.academy.modelNoteHint'])
    const runButton = panel.getByRole('button', {name: copy['ui.academy.modelRun'], exact: true})
    assert.equal(await runButton.getAttribute('aria-keyshortcuts'), 'Control+Enter Meta+Enter')
    assert.equal(await runButton.getAttribute('title'), await prompt.getAttribute('title'))
    assert.ok((await prompt.getAttribute('title')).includes(copy['ui.academy.modelShortcut'].split('{shortcut}')[0]))
    assert.equal(await prompt.inputValue(), initial)
    assert.equal(await node.evaluate(node => node.isConnected), true)
    for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 1000})
      await page.evaluate(theme => document.documentElement.className = theme, theme)
      const order = await prompt.evaluate(node => {
        const task = document.querySelector('[data-academy-task]')
        const quiz = document.querySelector('[data-academy-section="quiz"]')
        const notes = document.querySelector('[data-academy-section="notes"]')
        const tutor = document.querySelector('.academy-workbench-tutor')
        return {taskFirst: !!(task.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING),
          promptBeforeQuiz: !!(node.compareDocumentPosition(quiz) & Node.DOCUMENT_POSITION_FOLLOWING),
          promptBeforeNotes: !!(node.compareDocumentPosition(notes) & Node.DOCUMENT_POSITION_FOLLOWING),
          tutorBeforeQuiz: !!(tutor.compareDocumentPosition(quiz) & Node.DOCUMENT_POSITION_FOLLOWING)}
      })
      assert.deepEqual(order, {taskFirst: true, promptBeforeQuiz: true, promptBeforeNotes: true, tutorBeforeQuiz: true})
      assert.equal(await panel.getByRole('button', {name: copy['ui.academy.completeStudy'], exact: true}).count(), 0)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    }
  }
  assert.equal(modelWrites, 0)
  assert.equal(completions, 0)
  assert.deepEqual((await get('/projects/' + project.id + '/model-runs')).model_runs, [])
  await prompt.press('End')
  await prompt.press('Enter')
  assert.equal(await prompt.inputValue(), initial + '\n', 'Ordinary Enter inserts a new line')
  await prompt.fill(initial)
  for (const modifiers of [{ctrlKey: true, isComposing: true}, {metaKey: true, keyCode: 229},
    {ctrlKey: true, repeat: true}, {ctrlKey: true, shiftKey: true}, {metaKey: true, altKey: true}]) {
    await prompt.evaluate((node, modifiers) => node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true, ...modifiers})), modifiers)
  }
  await prompt.dispatchEvent('compositionstart')
  await prompt.press('Control+Enter')
  await prompt.dispatchEvent('compositionend')
  assert.equal(modelWrites, 0)
  let releaseRun
  const runGate = new Promise(resolve => {releaseRun = resolve; releases.push(resolve)})
  await page.route('**/api/v1/academy/projects/' + project.id + '/model-runs', async route => {
    if (route.request().method() !== 'POST') return route.continue()
    await runGate
    await route.continue()
  })
  const accepted = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/model-runs'))
  await prompt.press('Control+Enter')
  await accepted
  assert.equal(await panel.getByRole('button', {name: copy['ui.academy.modelRun'], exact: true}).isDisabled(), true)
  for (const metaKey of [false, true]) await prompt.evaluate((node, metaKey) => node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: !metaKey, metaKey, bubbles: true, cancelable: true})), metaKey)
  assert.equal(modelWrites, 1, 'Busy keyboard submissions must share the original single request')
  releaseRun()
  await panel.getByLabel(copy['ui.academy.modelResponse'], {exact: true}).waitFor()
  const recorded = (await get('/projects/' + project.id + '/model-runs')).model_runs[0]
  const question = panel.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true})
  const modelNote=panel.locator('[data-academy-section="notes"] textarea')
  await modelNote.fill('Observation draft / 保留观察记录')
  await question.fill('Why did this response follow my instructions? / 为什么这样回答？')
  const ask = panel.getByRole('button', {name: copy['ui.academy.tutor.explainResult'], exact: true})
  let tutorWrites = 0
  page.on('request', request => {if (request.method() === 'POST' && request.url().endsWith('/tutor-turns')) tutorWrites++})
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    const localized = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    await panel.getByText(localized['ui.academy.tutor.modelContext'], {exact: true}).waitFor()
    assert.equal(await modelNote.getAttribute('placeholder'),localized['ui.academy.modelNoteHint'])
    assert.equal(await modelNote.inputValue(),'Observation draft / 保留观察记录')
    assert.equal(await panel.locator('[data-academy-study-action]').textContent(), localized['ui.academy.studyCheckNext'])
  }
  await modelNote.fill('')
  assert.equal(await question.inputValue(), 'Why did this response follow my instructions? / 为什么这样回答？')
  assert.equal(tutorWrites, 0)
  await panel.getByRole('button', {name: copy['ui.academy.tutor.send'], exact: true}).click({trial: true})
  await question.press('Enter')
  const answer = panel.getByLabel(copy['ui.academy.tutor.answer'], {exact: true})
  await answer.waitFor()
  assert.ok((await answer.textContent()).includes(recorded.id))
  assert.ok((await answer.textContent()).includes('Own input / 用户输入保持原文。'))
  const turns = (await get('/projects/' + project.id + '/tutor-turns')).tutor_turns
  assert.equal(turns[0].status, 'completed')
  assert.ok(turns[0].response.includes(recorded.prompt))
  assert.ok(turns[0].response.includes(recorded.response))
  assert.deepEqual(turns[0].actions, [])
  assert.equal(tutorWrites, 1)
  assert.equal(modelWrites, 1, 'Asking about the result must not rerun the experiment')
  await prompt.fill(initial + '\nSECOND_EXPERIMENT')
  await panel.getByRole('button', {name: copy['ui.academy.modelRun'], exact: true}).click()
  await page.waitForFunction(({recordedId, label}) => {
    const result = document.querySelector('[data-model-run-id]')
    return result && result.dataset.modelRunId !== recordedId && [...result.querySelectorAll('[aria-label]')].some(node => node.getAttribute('aria-label') === label)
  }, {recordedId: recorded.id, label: copy['ui.academy.modelResponse']})
  assert.equal(modelWrites, 2)
  await prompt.fill(initial)
  await panel.getByRole('combobox', {name: copy['ui.academy.modelHistory'], exact: true}).selectOption(recorded.id)
  await ask.dispatchEvent('compositionstart')
  await ask.click()
  assert.equal(tutorWrites, 1)
  await ask.dispatchEvent('compositionend')
  const tutorEndpoint = '**/api/v1/academy/projects/' + project.id + '/tutor-turns'
  await page.route(tutorEndpoint, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    const response = await route.fetch()
    const receipt = await response.json()
    assert.equal(receipt.result.tutor_turn.model_run_id, recorded.id)
    await route.fulfill({json: {...receipt, result: {...receipt.result, tutor_turn: {...receipt.result.tutor_turn, model_run_id: 'wrong-record'}}}})
  })
  await ask.click()
  await panel.getByText(copy['ui.http.invalidResponse'], {exact: true}).waitFor()
  const pendingAnalysis = await page.evaluate(() => JSON.parse(sessionStorage.getItem('harness.academy.pending.v1')))
  assert.equal(pendingAnalysis.tutorInput.model_run_id, recorded.id)
  assert.equal(tutorWrites, 2)
  await page.unroute(tutorEndpoint)
  await panel.getByRole('button', {name: copy['ui.academy.recheckReceipt'], exact: true}).click()
  await page.waitForFunction(() => !sessionStorage.getItem('harness.academy.pending.v1'))
  const acceptedTurn = (await get('/projects/' + project.id + '/tutor-turns')).tutor_turns[0]
  await panel.locator('[data-tutor-turn-id="' + acceptedTurn.id + '"]').getByLabel(copy['ui.academy.tutor.answer'], {exact: true}).waitFor()
  const selectedTurn = (await get('/projects/' + project.id + '/tutor-turns')).tutor_turns[0]
  assert.equal(selectedTurn.status, 'completed')
  assert.equal(selectedTurn.model_run_id, recorded.id)
  assert.ok(selectedTurn.response.includes(recorded.prompt))
  assert.ok(!selectedTurn.response.includes('SECOND_EXPERIMENT'))
  assert.deepEqual(selectedTurn.actions, [])
  assert.equal(await question.inputValue(), 'Why did this response follow my instructions? / 为什么这样回答？')
  assert.equal(tutorWrites, 2)
  assert.equal(modelWrites, 2, 'Explaining a historical answer cannot rerun either experiment')
  for (const section of ['quiz', 'notes']) assert.notEqual(await panel.locator('[data-academy-section="' + section + '"]').getAttribute('open'), null)
  const complete = panel.locator('[data-academy-study-action]')
  await complete.waitFor()
  await complete.click({trial: true})
  assert.equal(await complete.getAttribute('data-study-step'), 'quiz')
  assert.equal(await complete.isDisabled(), false)
  const quizDetails = panel.locator('[data-academy-section="quiz"]')
  await quizDetails.locator(':scope > summary').click()
  await complete.dispatchEvent('compositionstart')
  await complete.click()
  assert.equal(await quizDetails.getAttribute('open'), null, 'Composition cannot navigate to a different field')
  await complete.dispatchEvent('compositionend')
  await complete.click()
  assert.notEqual(await quizDetails.getAttribute('open'), null)
  assert.equal(await quizDetails.locator('input').first().evaluate(node => node === document.activeElement), true)
  for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    await page.setViewportSize({width, height: 1000})
    await page.evaluate(theme => document.documentElement.className = theme, theme)
    await quizDetails.locator(':scope > summary').click()
    await complete.click()
    assert.notEqual(await quizDetails.getAttribute('open'), null)
    assert.equal(await quizDetails.locator('input').first().evaluate(node => node === document.activeElement), true)
    assert.equal(await question.inputValue(), 'Why did this response follow my instructions? / 为什么这样回答？')
    assert.equal(await prompt.inputValue(), initial)
    assert.equal(completions, 0)
    assert.equal(modelWrites, 2)
  }
  assert.equal(completions, 0, 'A generated response cannot complete the lesson')
  const currentCourse = (await get('/courses/llm-foundations')).course
  const currentLesson = currentCourse.lessons.find(item => item.id === lesson.id)
  for (const check of currentLesson.knowledge_checks) {
    const option = check.options.find(option => option.id === check.answer_id)
    await panel.getByRole('radio', {name: option.label, exact: true}).check()
    await panel.getByRole('button', {name: copy['ui.academy.checkUnderstanding'], exact: true}).click()
  }
  assert.equal(await complete.getAttribute('data-study-step'), 'notes')
  assert.equal(await complete.textContent(), copy['ui.academy.studyNoteNext'])
  const notesDetails = panel.locator('[data-academy-section="notes"]')
  await notesDetails.locator(':scope > summary').click()
  await complete.click()
  assert.notEqual(await notesDetails.getAttribute('open'), null)
  assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.note'], exact: true}).evaluate(node => node === document.activeElement), true)
  assert.equal(completions, 0, 'Guiding the next step must not complete the lesson')
  await panel.getByRole('textbox', {name: copy['ui.academy.note'], exact: true}).fill('I compared the actual reply with the supplied evidence / 我核对了实际回答与给定证据。')
  assert.equal(await complete.getAttribute('data-study-step'), 'complete')
  assert.equal(await complete.textContent(), copy['ui.academy.completeStudy'])
  await complete.click()
  await panel.getByText(copy['ui.academy.studyCompleted'], {exact: true}).waitFor()
  assert.equal(await complete.isDisabled(), true)
  assert.equal(await complete.getAttribute('data-study-step'), 'complete')
  assert.equal(completions, 1)
  assert.equal(modelWrites, 2)
  assert.equal((await get('/projects/' + project.id)).project.progress.find(row => row.lesson_id === lesson.id).state, 'completed')
  assert.deepEqual(errors, [])
  console.log('Academy model flow: prompt first, deferred reflection, original model evidence available in ordinary tutor questions, focus/draft preservation, no replay, explicit completion and same-instance en/zh/en responsive themes verified')
} catch (error) {
  failure = error
  if (page) try {console.error(await page.locator('#inspector-tab-content[data-panel="academy"]').innerText())} catch (diagnostic) {console.error(diagnostic)}
}
finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend, releases})
}
