import {requireAcademySandbox} from './academy-environment.mjs'
import {openAcademySection} from './academy-sections.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {mkdir, readFile} from 'node:fs/promises'
import {fixturePython} from './python-fixture.mjs'
import {launchBrowser} from './browser-scenario.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.academy_web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let diagnostics = '', browser, failure, timer
const releases = []
backend.stderr.on('data', data => {diagnostics += data})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Academy fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Academy fixture exited: ' + code + diagnostics)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (Number.isInteger(value.port)) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  const base = 'http://127.0.0.1:' + ready.port
  browser = await launchBrowser()
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}})
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(base)
  await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  await page.keyboard.press('Control+j')
  const helpGroup=page.locator('#inspector-tab-content[data-panel="home"] details').filter({hasText:'学习与帮助'})
  if ((await helpGroup.getAttribute('open')) === null) await helpGroup.locator('summary').click()
  await page.locator('#inspector-tab-content[data-panel="home"]').getByRole('button', {name: '学习中心', exact: true}).click()
  const panel = page.locator('#inspector-tab-content[data-panel="academy"]')
  await panel.locator('[data-academy-section="catalog"] > summary').waitFor()
  await openAcademySection(panel, 'catalog')
  await panel.getByRole('button', {name: '创建项目', exact: true}).waitFor()
  await panel.getByRole('textbox', {name: '项目名称', exact: true}).fill('Academy regression lab')
  await panel.getByRole('button', {name: '创建项目', exact: true}).click()
  const editor = panel.getByRole('textbox', {name: '练习代码', exact: true})
  await editor.waitFor()
  const disclose = async title => {
    const summary = panel.locator('summary').filter({hasText: title}).first()
    if (await summary.locator('..').getAttribute('open') === null) await summary.click()
  }
  assert.equal(await panel.getByRole('button', {name: '检查练习', exact: true}).isVisible(), true)
  assert.equal(await panel.getByRole('button', {name: '保存检查点', exact: true}).isVisible(), false)
  await openAcademySection(panel, 'manage')
  await disclose('学习路线（6 个阶段）')
  assert.equal(await panel.getByRole('list', {name: '学习阶段', exact: true}).locator(':scope > li').count(), 6)
  // 路线图选课只预览，不隐式替换当前项目文件。
  await panel.getByRole('button', {name: '第 5 课 · 把两句话排好队', exact: true}).click()
  await panel.getByRole('button', {name: '保存当前版本并打开本课', exact: true}).waitFor()
  await panel.getByRole('button', {name: /继续第 1 课/}).click()
  await editor.waitFor()
  const projects = await (await page.request.get(base + '/api/v1/academy/projects')).json()
  assert.equal(projects.projects.length, 1)
  const project = projects.projects[0]
  const progressBeforeQuiz = project.progress
  await openAcademySection(panel, 'quiz')
  await panel.getByRole('radio', {name: 'reply 会自动理解新问题并改变。', exact: true}).check()
  await openAcademySection(panel, 'quiz')
  await panel.getByRole('button', {name: '核对答案', exact: true}).click()
  await panel.getByText('换个角度，再试一次。', {exact: true}).waitFor()
  await panel.getByRole('button', {name: '查看提示 1/2', exact: true}).click()
  await panel.getByText('提示 1：先找两行赋值，再看 print。', {exact: true}).waitFor()
  await panel.getByRole('radio', {name: 'reply 保持原样。', exact: true}).check()
  await openAcademySection(panel, 'quiz')
  await panel.getByRole('button', {name: '核对答案', exact: true}).click()
  await panel.getByText('答对了。继续前，试着解释为什么。', {exact: true}).waitFor()
  assert.deepEqual((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id)).json()).project.progress, progressBeforeQuiz)
  assert.equal((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs.length, 0, 'Concept practice must not run code')
  await requireAcademySandbox(project)
  await panel.getByRole('button', {name: '检查练习', exact: true}).click()
  await panel.getByText('本课检查通过', {exact: true}).waitFor()
  await panel.getByRole('button', {name: '运行代码', exact: true}).click()
  await panel.getByRole('status').filter({hasText: /^执行完成$/}).waitFor()
  await openAcademySection(panel, 'run-evidence')
  await panel.getByText('宿主执行事实', {exact: true}).first().waitFor()
  await editor.waitFor()
  await page.waitForFunction(() => document.querySelector('textarea[aria-label="练习代码"]')?.value.includes('reply ='))
  const original = await editor.inputValue()
  // 修改后直接执行；同实例语言切换不保存、不运行，也不改写草稿。
  const runnableDraft = original + '\nprint("SAVED_DRAFT_EXECUTED")\n'
  await editor.fill(runnableDraft)
  let combinedPosts = 0
  const countCombined = request => {
    if (request.method() === 'POST' && /\/(run|check)$/.test(request.url())) combinedPosts++
  }
  page.on('request', countCombined)
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language, language)
    await page.waitForFunction(() => !document.getElementById('language-preference').disabled)
    await page.keyboard.press('Control+,')
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 1000})
      await page.evaluate(theme => document.documentElement.classList.toggle('dark', theme === 'dark'), theme)
      for (const key of ['saveAndRun', 'saveAndCheck']) {
        const button = panel.getByRole('button', {name: copy['ui.academy.' + key], exact: true})
        await button.waitFor()
        await page.waitForFunction(label => [...document.querySelectorAll('#inspector-tab-content[data-panel="academy"] button')].some(button => button.textContent === label && !button.disabled), copy['ui.academy.' + key])
        assert.equal(await button.isEnabled(), true)
      }
      assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.source'], exact: true}).inputValue(), runnableDraft)
    }
  }
  assert.equal(combinedPosts, 0)
  // 同一个事件中重复点击仍只运行一次，输入法组合期间不执行。
  const saveRun = panel.getByRole('button', {name: 'Run code', exact: true})
  await saveRun.evaluate(button => {
    button.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}))
    button.click()
    button.dispatchEvent(new CompositionEvent('compositionend', {bubbles: true}))
  })
  assert.equal(combinedPosts, 0)
  await saveRun.evaluate(button => {button.click(); button.click()})
  await panel.getByRole('status').filter({hasText: /^Run completed$/}).waitFor()
  assert.equal(combinedPosts, 1)
  assert.ok((await panel.locator('pre').allTextContents()).some(text => text.includes('SAVED_DRAFT_EXECUTED')))
  const englishEditor = panel.getByRole('textbox', {name: 'Practice code', exact: true})
  await englishEditor.fill(original)
  // 保存并检查丢失响应后刷新，只回查原回执，不重放保存或执行。
  const checkPath = '**/api/v1/academy/projects/' + project.id + '/check'
  await page.route(checkPath, async route => {await route.fetch(); await route.abort('failed')})
  const lostCheck = page.waitForEvent('requestfailed', request => request.method() === 'POST' && request.url().endsWith('/check'))
  await panel.getByRole('button', {name: 'Check exercise', exact: true}).click()
  await lostCheck
  assert.equal(combinedPosts, 2)
  await page.unroute(checkPath)
  const newerDraft = original + '\n# A newer draft from another window\n'
  await page.evaluate(({projectId, content}) => {
    const pending = JSON.parse(sessionStorage.getItem('harness.academy.pending.v1'))
    const saved = pending.executionInput.source
    localStorage.setItem('harness.academy.draft.v1:' + JSON.stringify([projectId, saved.path]), JSON.stringify({content, version: saved.expected_version}))
  }, {projectId: project.id, content: newerDraft})
  await page.reload()
  await page.getByRole('textbox', {name: 'Input message', exact: true}).waitFor()
  if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
  await panel.getByRole('button', {name: 'Check operation receipt', exact: true}).click()
  await page.waitForFunction(() => !sessionStorage.getItem('harness.academy.pending.v1'))
  await page.waitForFunction(content => document.querySelector('textarea[aria-label="Practice code"]')?.value === content, newerDraft)
  assert.equal(await englishEditor.inputValue(), newerDraft, 'The original receipt must preserve a newer cross-window draft')
  await panel.getByRole('button', {name: 'Discard unsaved changes', exact: true}).click()
  await panel.getByText('Lesson checks passed', {exact: true}).waitFor()
  assert.equal(combinedPosts, 2)
  const latestRun = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs[0]
  const savedProject = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id)).json()).project
  assert.equal(latestRun.input_fingerprint, savedProject.fingerprint)
  page.off('request', countCombined)
  await page.keyboard.press('Control+,')
  await page.locator('#language-preference').selectOption('zh-CN')
  await page.waitForFunction(() => document.documentElement.lang === 'zh-CN' && !document.getElementById('language-preference').disabled)
  await page.keyboard.press('Control+,')
  const changed = original.replace(/^reply = .+$/m, 'reply = ""')
  assert.notEqual(changed, original)
  await editor.fill(changed)
  await panel.getByText('此前运行 · 代码已变化，请重新运行以核对当前代码。', {exact: true}).waitFor()
  // 刷新和隐藏不丢失草稿。
  await page.reload()
  await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
  await editor.waitFor()
  assert.equal(await editor.inputValue(), changed)
  let saves = 0
  const savePath = '**/api/v1/academy/projects/' + project.id + '/files'
  await page.route(savePath, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    saves++
    await route.fetch()
    await route.abort('failed')
  })
  const lostSave = page.waitForEvent('requestfailed', request => request.method() === 'POST' && request.url().endsWith('/files'))
  await openAcademySection(panel, 'code-options')
  await panel.getByRole('button', {name: '保存修改', exact: true}).click()
  await lostSave
  const ownPending = await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1'))
  assert.ok(ownPending, 'An uncertain write must retain its receipt identity')
  const otherTab = await page.context().newPage()
  await otherTab.goto(base)
  assert.equal(await otherTab.evaluate(() => localStorage.getItem('harness.academy.view.v1')), await page.evaluate(() => localStorage.getItem('harness.academy.view.v1')))
  assert.equal(await otherTab.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1')), null, 'Another tab must not own or clear this pending operation')
  await otherTab.close()
  assert.equal(await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1')), ownPending)
  await panel.getByRole('button', {name: '回查操作回执', exact: true}).waitFor()
  await page.unroute(savePath)
  await panel.getByRole('button', {name: '回查操作回执', exact: true}).click()
  await page.waitForFunction(() => !document.body.textContent.includes('操作结果未确认，已保留请求号'))
  assert.equal(saves, 1, 'An unconfirmed save must not be replayed')
  await panel.getByRole('button', {name: '检查练习', exact: true}).click()
  await panel.getByText('本课检查未通过', {exact: true}).waitFor()
  await disclose('资料与历史')
  await panel.getByRole('button', {name: /day-01-input-output · 课前版本/}).click()
  await panel.getByRole('button', {name: '保存当前版本并恢复', exact: true}).waitFor()
  assert.ok((await panel.locator('pre').allTextContents()).some(text => text.includes('reply = ""')))
  await panel.getByRole('button', {name: '保存当前版本并恢复', exact: true}).click()
  await page.waitForFunction(original => document.querySelector('textarea[aria-label="练习代码"]')?.value === original, original)
  await panel.getByRole('button', {name: '检查练习', exact: true}).click()
  await panel.getByText('本课检查通过', {exact: true}).waitFor()
  // 连续读取恢复预览时，前一请求必须取消且不能覆盖当前选择。
  const checkpoints = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/checkpoints')).json()).checkpoints
  const firstCheckpoint = checkpoints.find(item => item.kind === 'before-restore')
  const lessonCheckpoint = checkpoints.find(item => item.kind === 'lesson')
  let releaseFirst, firstSeen, firstDone
  const released = new Promise(resolve => {releaseFirst = resolve})
  const observed = new Promise(resolve => {firstSeen = resolve})
  const previewPath = '**/api/v1/academy/projects/' + project.id + '/restore?checkpoint_id=' + firstCheckpoint.id
  const firstHandled = new Promise(resolve => {firstDone = resolve})
  await page.route(previewPath, async route => {
    firstSeen()
    await released
    try {await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({
      project_id: project.id, checkpoint_id: firstCheckpoint.id, fingerprint: '0'.repeat(64),
      changes: [{path: 'old.py', diff: 'STALE PREVIEW MUST NOT APPEAR'}],
    })})} catch (error) {if (!route.request().failure()) throw error} finally {firstDone()}
  })
  await panel.getByRole('button', {name: new RegExp(firstCheckpoint.id.slice(-6))}).click()
  await observed
  const cancelledPreview = page.waitForEvent('requestfailed', request => request.url().includes(firstCheckpoint.id))
  await panel.getByRole('button', {name: new RegExp(lessonCheckpoint.id.slice(-6))}).click()
  await cancelledPreview
  await panel.getByText('文件内容一致。', {exact: true}).waitFor()
  releaseFirst()
  await firstHandled
  await page.unroute(previewPath)
  assert.equal(await panel.getByText('STALE PREVIEW MUST NOT APPEAR', {exact: true}).count(), 0)
  await panel.getByRole('button', {name: '取消', exact: true}).click()

  // 提交是只读快照点评；坏语法可以提交，不会隐式运行或把课程标为通过。
  const broken = 'question = "I can submit unfinished code"\nreply = (\n'
  await editor.fill(broken)
  const savedBroken = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/files'))
  await openAcademySection(panel, 'code-options')
  await panel.getByRole('button', {name: '保存修改', exact: true}).click()
  assert.equal((await (await savedBroken).json()).status, 'ready')
  await page.waitForFunction(() => !sessionStorage.getItem('harness.academy.pending.v1'))
  const note = 'I predicted a reply, changed the code, and still need to repair the syntax.'
  await openAcademySection(panel, 'notes')
  await openAcademySection(panel, 'feedback')
  const noteEditor = panel.getByRole('textbox', {name: '学习说明', exact: true})
  await noteEditor.fill(note)
  let submits = 0
  page.on('request', request => {if (request.method() === 'POST' && request.url().endsWith('/submissions')) submits++})
  await noteEditor.dispatchEvent('compositionstart')
  await noteEditor.dispatchEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 229, isComposing: true, bubbles: true})
  assert.equal(submits, 0, 'IME composition must not submit a lesson')
  // 隐藏会丢失浏览器的 compositionend；重开后不能残留“正在组合”的提交锁。
  await page.keyboard.press('Control+j')
  await page.waitForFunction(() => document.querySelector('#task-inspector')?.getAttribute('aria-hidden') === 'true')
  await page.keyboard.press('Control+j')
  await openAcademySection(panel, 'notes')
  await openAcademySection(panel, 'feedback')
  await noteEditor.waitFor()
  assert.equal(await noteEditor.inputValue(), note)
  const runsBeforeSubmit = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs.length
  const submittedResponse = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/submissions'))
  await panel.getByRole('button', {name: '提交并获取点评', exact: true}).click()
  const submitted = (await (await submittedResponse).json()).result.submission
  const feedback = panel.locator('[data-submission-id="' + submitted.id + '"]')
  await openAcademySection(panel, 'feedback')
  await feedback.getByRole('img', {name: /五维学习观察/}).waitFor()
  assert.equal(await feedback.locator('circle[data-dimension]').count(), 5)
  await openAcademySection(panel, 'feedback')
  assert.equal(await feedback.getByRole('img', {name: '3 / 5 星', exact: true}).count(), 5)
  await feedback.locator('summary').getByText('依据', {exact: true}).first().click()
  await feedback.locator('blockquote').filter({hasText: note}).first().waitFor()
  const completed = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/submissions/' + submitted.id)).json()).submission
  assert.equal(completed.status, 'completed')
  assert.equal(completed.language, 'zh-CN')
  assert.equal((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs.length, runsBeforeSubmit)
  const assessedProject = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id)).json()).project
  assert.equal(assessedProject.progress[0].state, 'in_progress', 'AI feedback must not award a passing state')
  assert.equal(submits, 1)

  // 已完成点评保留提交时的代码；恢复当前代码不会改写原点评。
  await disclose('资料与历史')
  await panel.getByRole('button', {name: new RegExp(lessonCheckpoint.id.slice(-6))}).click()
  await panel.getByRole('button', {name: '保存当前版本并恢复', exact: true}).click()
  await page.waitForFunction(original => document.querySelector('textarea[aria-label="练习代码"]')?.value === original, original)
  assert.deepEqual((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/submissions/' + submitted.id)).json()).submission.assessment, completed.assessment)

  // 等待中的点评观察在隐藏时取消，停止后续轮询，迟到响应不能重新发布。
  await page.clock.install({time: new Date(0)})
  await page.clock.pauseAt(new Date(86400000))
  const submissionsPath = '**/api/v1/academy/projects/' + project.id + '/submissions'
  const pendingSubmission = {...completed, status: 'evaluating'}
  delete pendingSubmission.assessment
  let submissionReads = 0, releaseHidden, hiddenEntered, hiddenHandled
  const hiddenGate = new Promise(resolve => {releaseHidden = resolve})
  const hiddenObserved = new Promise(resolve => {hiddenEntered = resolve})
  const hiddenDone = new Promise(resolve => {hiddenHandled = resolve})
  releases.push(releaseHidden)
  await page.route(submissionsPath, async route => {
    submissionReads++
    if (submissionReads === 1) return route.fulfill({json: {submissions: [pendingSubmission]}})
    hiddenEntered()
    await hiddenGate
    try {await route.fulfill({json: {submissions: [completed]}})}
    catch (error) {if (!route.request().failure()) throw error}
    finally {hiddenHandled()}
  })
  await panel.getByRole('button', {name: '刷新学习状态', exact: true}).click()
  await feedback.getByText('AI 正在阅读你的代码和说明…', {exact: true}).waitFor()
  await page.clock.runFor(2100)
  await hiddenObserved
  const cancelledHidden = page.waitForEvent('requestfailed', request => request.method() === 'GET' && request.url().endsWith('/submissions'))
  await page.keyboard.press('Control+j')
  await cancelledHidden
  const hiddenReads = submissionReads
  await page.clock.runFor(6100)
  assert.equal(submissionReads, hiddenReads, 'A hidden Academy must not poll submissions')
  releaseHidden()
  await hiddenDone
  await page.unroute(submissionsPath)
  await page.keyboard.press('Control+j')
  await openAcademySection(panel, 'feedback')
  await feedback.getByRole('img', {name: /五维学习观察/}).waitFor()

  // 切换项目后的迟到旧点评不能覆盖新项目。
  const other = (await (await page.request.post(base + '/api/v1/academy/projects', {data: {
    request_id: 'browser-other-project', course_id: project.course_id, name: 'Other assessment lab',
  }})).json()).result.project
  await panel.getByRole('button', {name: '刷新学习状态', exact: true}).click()
  await disclose('课程与项目')
  const projectChoice = panel.getByRole('combobox', {name: '我的学习项目', exact: true})
  await projectChoice.locator('option[value="' + other.id + '"]').waitFor({state: 'attached'})
  let releaseOld, oldEntered, oldHandled
  const oldGate = new Promise(resolve => {releaseOld = resolve})
  const oldObserved = new Promise(resolve => {oldEntered = resolve})
  const oldDone = new Promise(resolve => {oldHandled = resolve})
  releases.push(releaseOld)
  await page.route(submissionsPath, async route => {
    oldEntered()
    await oldGate
    const stale = {...completed, assessment: {...completed.assessment, summary: 'STALE PROJECT FEEDBACK'}}
    try {await route.fulfill({json: {submissions: [stale]}})}
    catch (error) {if (!route.request().failure()) throw error}
    finally {oldHandled()}
  })
  await panel.getByRole('button', {name: '刷新学习状态', exact: true}).click()
  await oldObserved
  const cancelledOld = page.waitForEvent('requestfailed', request => request.method() === 'GET' && request.url().endsWith('/submissions'))
  await projectChoice.selectOption(other.id)
  await cancelledOld
  await page.waitForFunction(id => JSON.parse(localStorage.getItem('harness.academy.view.v1')).projectId === id, other.id)
  releaseOld()
  await oldDone
  await page.clock.runFor(32)
  assert.equal(await panel.getByText('STALE PROJECT FEEDBACK', {exact: true}).count(), 0)
  assert.equal(await panel.locator('[data-submission-id="' + submitted.id + '"]').count(), 0)
  await page.unroute(submissionsPath)
  await projectChoice.selectOption(project.id)
  await openAcademySection(panel, 'feedback')
  await feedback.getByRole('img', {name: /五维学习观察/}).waitFor()
  await page.clock.resume()

  // 同一个浏览器实例执行英文→中文→英文，并保留项目身份。
  for (const language of ['en', 'zh-CN', 'en']) {
    const saved = await page.request.patch(base + '/api/v1/ui-settings', {data: {language}})
    assert.ok(saved.ok())
    for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
      await page.setViewportSize({width, height: 1000})
      await page.evaluate(theme => localStorage.setItem('harness.ui-theme', theme), theme)
      await page.reload()
      const inputName = language === 'en' ? 'Input message' : '输入消息'
      await page.getByRole('textbox', {name: inputName, exact: true}).waitFor()
      if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
      await panel.getByRole('heading', {name: language === 'en' ? 'Lesson 1 · First sight: input and output' : '第 1 课 · 先看见：输入和输出', exact: true}).waitFor()
      const bounds = await page.evaluate(() => ({viewport: innerWidth, scroll: document.documentElement.scrollWidth}))
      assert.ok(bounds.scroll <= bounds.viewport, 'Academy must fit ' + [language, theme, width].join('/'))
      await disclose(language === 'en' ? 'Courses and projects' : '课程与项目')
      assert.equal(await panel.getByRole('combobox', {name: language === 'en' ? 'My learning projects' : '我的学习项目', exact: true}).inputValue(), project.id)
      assert.equal(await panel.getByRole('textbox', {name: language === 'en' ? 'Practice code' : '练习代码', exact: true}).inputValue(), original)
      await openAcademySection(panel, 'feedback')
      await feedback.getByRole('img', {name: language === 'en' ? /Five learning dimensions/ : /五维学习观察/}).waitFor()
      assert.ok((await feedback.textContent()).includes('zh-CN'), 'Stored feedback language must remain frozen')
    }
  }
  if (process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-en-dark-wide.png'})
    await panel.getByRole('img', {name: /Five learning dimensions/}).scrollIntoViewIfNeeded()
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/academy-feedback-en-dark-wide.png'})
  }
  assert.deepEqual(errors, [])
  console.log('Academy: create, run, edit, unconfirmed save, check, restore, frozen AI feedback, polling ownership and language/theme/width matrix verified')
} catch (error) {failure = error}
finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend, releases})
}
