import {chooseFixtureProject} from './conversation-project.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {once} from 'node:events'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile, mkdir} from 'node:fs/promises'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.goal_mode_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
let diagnostics = '', browser, startupTimer, activePage
backend.stderr.on('data', data => {diagnostics += data})
const lines = createInterface({input: backend.stdout})
try {
  const ready = await new Promise((resolve, reject) => {
    startupTimer = setTimeout(() => reject(Error('Goal fixture startup timed out\n' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Goal fixture exited: ' + code + '\n' + diagnostics)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (Number.isInteger(value.port)) resolve(value)} catch { /* 忽略启动诊断。 */ }})
  })
  clearTimeout(startupTimer)
  browser = await launchBrowser()
  const baseURL = 'http://127.0.0.1:' + ready.port
  for (const language of ['zh-CN', 'en']) {
    const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url)))
    const t = key => translations[key]
    for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
      const page = await browser.newPage({viewport: {width, height: 900}})
      activePage = page
      const errors = [], sends = []
      let sessionId, goalWrites = 0
      page.on('pageerror', error => errors.push(error.message))
      page.on('request', request => {if (/\/api\/v1\/sessions\/[^/]+\/goal$/.test(new URL(request.url()).pathname) && request.method() === 'POST') goalWrites++})
      page.on('websocket', socket => {
        sessionId = new URL(socket.url()).searchParams.get('session')
        socket.on('framesent', ({payload}) => {try {const value = JSON.parse(String(payload)); if (value.action === 'send') sends.push(value.text)} catch { /* 忽略非提交帧。 */ }})
      })
      assert.ok((await page.request.patch(baseURL + '/api/v1/ui-settings', {data: {language}})).ok())
      await page.addInitScript(({language, theme}) => {
        localStorage.setItem('harness.language.effective.v1', language)
        localStorage.setItem('harness.ui-theme', theme)
      }, {language, theme})
      await page.goto(baseURL)
      const input = page.getByRole('textbox', {name: t('ui.components.chat.Composer.a410452649b3'), exact: true})
      await input.waitFor(); await input.evaluate(element => element.blur()); await page.keyboard.press('ControlOrMeta+Shift+u')
      await chooseFixtureProject(page)
      const removeGoal = page.getByRole('button', {name: t('ui.composerMode.removeGoal'), exact: true})
      const removePlan = page.getByRole('button', {name: t('ui.composerMode.removePlan'), exact: true})
      const form = page.getByRole('form', {name: t('ui.components.chat.GoalDock.1d6e17611a7e'), exact: true})
      const dock = page.getByRole('region', {name: t('ui.components.chat.GoalDock.34089409c6c1'), exact: true})
      async function send(text) {
        await input.fill(text)
        await input.press('Enter')
        // 输入清空发生在提交锁释放后，避免下一条命令被准入保护丢弃。
        await page.waitForFunction(() => document.querySelector('.composer-editor')?.value === '')
      }
      async function idle() {await page.waitForFunction(() => !document.querySelector('.composer-editor')?.getAttribute('aria-describedby'))}
      async function goal() {return (await (await page.request.get(baseURL + '/api/v1/sessions/' + sessionId + '/goal')).json()).goal}
      async function stableTitle() {
        await page.waitForFunction(() => document.querySelector('#project-sidebar [aria-current="page"]')?.getAttribute('aria-label') === 'first ordinary goal fixture')
        await page.getByRole('tab', {name: 'first ordinary goal fixture', exact: true}).waitFor()
        const catalog = await (await page.request.get(baseURL + '/api/v1/sessions')).json()
        assert.equal(catalog.sessions.find(session => session.id === sessionId).title, 'first ordinary goal fixture')
      }
      async function screenshot(stage) {
        if (!process.env.HARNESS_TEST_SCREENSHOTS) return
        await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
        await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/goal-' + [stage, language, theme, width].join('-') + '.png'})
      }
      await send('/goal')
      await form.waitFor()
      assert.equal(sends.length, 0, 'Selecting a goal must open settings without submitting a task')
      assert.equal(goalWrites, 0)
      await form.getByRole('textbox').fill('unsent goal settings draft')
      await form.locator('details > summary').click()
      await form.getByRole('spinbutton').first().fill('7')
      await form.getByRole('spinbutton').last().fill('12345')
      await page.reload()
      await input.waitFor()
      await send('/goal')
      await form.waitFor()
      assert.equal(await form.getByRole('textbox').inputValue(), 'unsent goal settings draft', 'Goal settings must survive reload without submitting a task')
      await form.locator('details > summary').click()
      assert.equal(await form.getByRole('spinbutton').first().inputValue(), '7')
      assert.equal(await form.getByRole('spinbutton').last().inputValue(), '12345')
      assert.equal(goalWrites, 0)
      await form.getByRole('button', {name: t('ui.components.chat.GoalDock.2cd0f3be8738'), exact: true}).click()
      await removeGoal.waitFor({state: 'detached'})
      await send('/goal'); await form.waitFor()
      assert.equal(await form.getByRole('textbox').inputValue(), '', 'Cancel must discard saved goal settings')
      await form.getByRole('textbox').fill('discarded when selecting plan')
      await send('/plan')
      await form.waitFor({state: 'detached'})
      assert.equal(await removeGoal.count(), 0)
      await removePlan.focus(); await page.keyboard.press('Enter')
      // 首次人工输入建立稳定标题，后续目标只更新任务摘要。
      await send('/plan first ordinary goal fixture')
      await page.getByText('fixture response verified', {exact: true}).waitFor()
      await idle()
      await stableTitle()
      await page.reload()
      await input.waitFor()
      await stableTitle()
      await removePlan.focus(); await page.keyboard.press('Enter')
      await removePlan.waitFor({state: 'detached'})
      await send('/goal'); await form.waitFor()
      assert.equal(await form.getByRole('textbox').inputValue(), '', 'Selecting plan must discard the canceled goal settings')
      await form.getByRole('textbox').fill('goal fixture complete')
      const beforeCreate = goalWrites
      await page.route('**/api/v1/sessions/*/goal', async route => {
        if (route.request().method() !== 'POST') return route.continue()
        await route.fetch()
        await route.abort('failed')
      })
      await form.getByRole('button', {name: t('ui.components.chat.GoalDock.781565e70304'), exact: true}).click()
      await dock.getByRole('status').filter({hasText: t('ui.lib.goalActivity.f28461bb49c8')}).waitFor()
      await idle()
      assert.equal(goalWrites - beforeCreate, 1, 'A lost create response must be read back without replaying the write')
      await removeGoal.waitFor({state: 'detached'})
      assert.equal(await form.count(), 0, 'A confirmed creation must close its settings form')
      await page.unroute('**/api/v1/sessions/*/goal')
      const completed = await goal()
      assert.equal(completed.phase, 'complete')
      assert.equal(completed.completion[0].evidence[0].name, 'read')
      await input.fill('unsent goal draft preserved')
      await screenshot('complete')
      await page.reload()
      await input.waitFor()
      assert.equal(await input.inputValue(), 'unsent goal draft preserved')
      assert.equal(await removeGoal.count(), 0)
      await send('ordinary after completion')
      await idle()
      assert.equal(sends.at(-1), 'ordinary after completion')
      assert.equal((await goal()).id, completed.id)
      await send('/goal'); await form.waitFor()
      await form.getByRole('textbox').fill('goal fixture waiting')
      await form.getByRole('button', {name: t('ui.components.chat.GoalDock.781565e70304'), exact: true}).click()
      const question = page.locator('section[id^="q-"]').filter({hasText: 'Goal fixture is waiting'})
      await question.waitFor()
      await dock.getByRole('button', {name: t('ui.components.chat.GoalDock.34a8a93ff639'), exact: true}).click()
      await dock.getByRole('status').filter({hasText: t('ui.lib.goalActivity.eb0c326b60ae')}).waitFor()
      const paused = await goal()
      await send('/goal')
      assert.equal(await form.count(), 0, 'An unfinished paused goal must not open a replacement form')
      await send('paused ordinary supplement')
      assert.equal(sends.at(-1), 'paused ordinary supplement')
      await question.getByRole('button', {name: 'Finish turn', exact: true}).click()
      await question.getByRole('button', {name: t('ui.components.chat.blocks.QuestionCard.0c1762dddc4f'), exact: true}).click()
      await idle()
      assert.equal((await goal()).id, paused.id)
      assert.equal((await goal()).phase, 'paused')
      await removeGoal.waitFor({state: 'detached'})
      await screenshot('paused')
      await send('/goal clear')
      await dock.waitFor({state: 'detached'})
      await send('/goal goal fixture blocked')
      await dock.getByRole('status').filter({hasText: t('ui.lib.goalActivity.b7e3e715f18b')}).waitFor()
      await idle()
      const blocked = await goal()
      await stableTitle()
      await send('/goal'); await send('blocked ordinary supplement'); await idle()
      assert.equal(sends.at(-1), 'blocked ordinary supplement')
      assert.equal((await goal()).id, blocked.id)
      assert.equal((await goal()).phase, 'blocked')
      // 真实控制接口不依赖前端瞬时 running 值，固定晚应用引导的顺序。
      async function control(action, objective) {
        const current = await goal()
        const response = await page.request.post(baseURL + '/api/v1/sessions/' + sessionId + '/goal', {data: {
          action, ...(action === 'create' ? {objective, max_goal_rounds: null} : {goal_id: current.id, revision: current.revision}),
        }})
        assert.ok(response.ok(), await response.text())
      }
      await control('clear')
      await dock.waitFor({state: 'detached'})
      const oldQuestionIds = await page.locator('section[id^="q-"]').evaluateAll(elements => elements.map(element => element.id))
      await control('create', 'goal fixture waiting')
      await page.waitForFunction(ids => [...document.querySelectorAll('section[id^="q-"]')].some(element => !ids.includes(element.id)), oldQuestionIds)
      const lateQuestion = page.locator('section[id^="q-"]').filter({hasText: 'Goal fixture is waiting'}).last()
      await lateQuestion.waitFor()
      await control('pause')
      await control('clear')
      await control('create', 'goal fixture blocked')
      await lateQuestion.getByRole('button', {name: 'Finish turn', exact: true}).click()
      await lateQuestion.getByRole('button', {name: t('ui.components.chat.blocks.QuestionCard.0c1762dddc4f'), exact: true}).click()
      await dock.getByRole('status').filter({hasText: t('ui.lib.goalActivity.b7e3e715f18b')}).waitFor()
      await idle()
      const catalog = await (await page.request.get(baseURL + '/api/v1/sessions')).json()
      assert.equal(catalog.sessions.find(session => session.id === sessionId).summary, 'goal fixture blocked',
        'Late internal goal control must not replace the task summary')
      await stableTitle()
      await screenshot('late-control')
      await page.reload()
      await input.waitFor()
      await stableTitle()
      const reopenedCatalog = await (await page.request.get(baseURL + '/api/v1/sessions')).json()
      assert.equal(reopenedCatalog.sessions.find(session => session.id === sessionId).summary, 'goal fixture blocked')
      assert.equal(await page.locator('html').getAttribute('lang'), language)
      assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      assert.deepEqual(errors, [])
      await page.close()
      console.log('Goal mode lifecycle passed:', language, theme, width)
    }
  }
} catch (error) {
  console.error(diagnostics)
  if (activePage && !activePage.isClosed()) console.error((await activePage.locator('body').innerText()).slice(-7000))
  throw error
} finally {
  clearTimeout(startupTimer)
  try {await browser?.close()} finally {
    lines.close()
    if (backend.exitCode === null && backend.signalCode === null) {
      const exited = once(backend, 'exit')
      backend.kill('SIGTERM')
      const timer = setTimeout(() => backend.kill('SIGKILL'), 20000)
      await exited.finally(() => clearTimeout(timer))
    }
  }
}
