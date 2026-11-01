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
const backend = spawn(fixturePython, ['-m', 'tests.plan_mode_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
let diagnostics = '', browser, startupTimer, activePage
backend.stderr.on('data', data => {diagnostics += data; if (process.env.HARNESS_TEST_DIAGNOSTICS) process.stderr.write(data)})
const lines = createInterface({input: backend.stdout})
try {
  const ready = await new Promise((resolve, reject) => {
    startupTimer = setTimeout(() => reject(Error('Plan fixture startup timed out\n' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Plan fixture exited: ' + code + '\n' + diagnostics)))
    lines.on('line', line => {
      try {const value = JSON.parse(line); if (Number.isInteger(value.port)) resolve(value)} catch { /* 启动诊断不是就绪回执。 */ }
    })
  })
  clearTimeout(startupTimer)
  browser = await launchBrowser()
  const baseURL = 'http://127.0.0.1:' + ready.port
  for (const language of ['zh-CN', 'en']) {
    const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url)))
    const t = key => translations[key]
    for (const theme of ['light', 'dark']) {
      for (const width of [390, 1440]) {
        const page = await browser.newPage({viewport: {width, height: 900}})
        activePage = page
        const errors = [], sends = []
        let sessionId
        page.on('pageerror', error => errors.push(error.message))
        page.on('websocket', socket => {
          sessionId = new URL(socket.url()).searchParams.get('session')
          socket.on('framesent', ({payload}) => {
            try {const data = JSON.parse(String(payload)); if (data.action === 'send') sends.push(data.text)} catch { /* 非提交帧不参与断言。 */ }
          })
        })
        assert.ok((await page.request.patch(baseURL + '/api/v1/ui-settings', {data: {language}})).ok())
        await page.addInitScript(({language, theme}) => {
          localStorage.setItem('harness.language.effective.v1', language)
          localStorage.setItem('harness.ui-theme', theme)
        }, {language, theme})
        await page.goto(baseURL)
        await page.locator('.composer-editor').waitFor()
        await page.locator('.composer-editor').evaluate(element => element.blur())
        await page.keyboard.press('ControlOrMeta+Shift+u')
        await chooseFixtureProject(page)
        const input = page.getByRole('textbox', {name: t('ui.components.chat.Composer.a410452649b3'), exact: true})
        const remove = page.getByRole('button', {name: t('ui.composerMode.removePlan'), exact: true})
        const readOnly = page.getByRole('status').filter({hasText: t('ui.components.chat.Composer.77bdeab7a5b5')})
        const approve = page.getByRole('button', {name: t('ui.components.chat.blocks.QuestionCard.b168fdafe0f4'), exact: true}).and(page.locator(':enabled'))
        async function send(text) {await input.fill(text); await input.press('Enter')}
        await send('/plan')
        await remove.waitFor()
        await page.getByRole('status').filter({hasText: t('ui.composerMode.planHint')}).waitFor()
        assert.equal(await readOnly.count(), 0, 'Selecting a plan must not claim read-only enforcement before sending')
        assert.equal(sends.length, 0, 'Selecting a mode must not submit a task')
        await remove.focus(); await page.keyboard.press('Enter')
        await send('first ordinary fixture message')
        await page.getByText('fixture response verified', {exact: true}).waitFor()
        await send('/plan')
        await send('plan fixture request')
        const clarification = page.locator('section[id^="q-"]').filter({hasText: 'Fixture scope clarification'})
        await clarification.getByRole('button', {name: 'Current scope', exact: true}).click()
        await readOnly.waitFor()
        // 计划进行中排队的补充消息不得携带新的 /plan 意图。
        await send('queued ordinary supplement')
        assert.equal(sends.at(-1), 'queued ordinary supplement')
        await clarification.getByRole('button', {name: t('ui.components.chat.blocks.QuestionCard.0c1762dddc4f'), exact: true}).click()
        await approve.waitFor()
        const decisionVisible = await approve.evaluate(element => {
          const button = element.getBoundingClientRect()
          const panel = element.closest('[role="region"]')?.getBoundingClientRect()
          return {visible: Boolean(panel && button.top >= panel.top && button.bottom <= panel.bottom),
            button: {top: button.top, bottom: button.bottom}, panel: panel && {top: panel.top, bottom: panel.bottom}}
        })
        assert.ok(decisionVisible.visible, 'A long plan must keep its decision controls visible without scrolling the pending panel: ' + JSON.stringify(decisionVisible))
        const feedbackField = page.getByRole('textbox', {name: t('ui.components.chat.blocks.QuestionCard.6a612d9e2134'), exact: true}).and(page.locator(':enabled'))
        assert.equal(await feedbackField.isVisible(), false, 'Empty optional revision comments must start collapsed')
        assert.equal(await remove.count(), 1, 'Answering a clarification must keep plan mode')
        await page.getByRole('button', {name: t('ui.components.chat.blocks.QuestionCard.b367cdfb55b5'), exact: true}).and(page.locator(':enabled')).click()
        await page.getByText('Fixture implementation plan 2', {exact: true}).waitFor()
        await approve.waitFor()
        assert.equal(await readOnly.count(), 1, 'Requesting revisions must keep the actual plan hint')
        const comments = page.getByRole('region', {name: t('ui.components.chat.Conversation.56897d5961b4'), exact: true})
          .locator('summary').filter({hasText: t('ui.components.chat.blocks.QuestionCard.6a529601daca')})
        await comments.focus(); await page.keyboard.press('Enter')
        await feedbackField.fill('保留当前范围，尚未提交的修改意见')
        assert.equal(await page.getByRole('region', {name: t('ui.components.chat.Conversation.56897d5961b4'), exact: true})
          .getByRole('button', {name: t('ui.components.chat.blocks.QuestionCard.b168fdafe0f4'), exact: true}).isDisabled(), true)
        await page.reload()
        await feedbackField.waitFor()
        assert.equal(await feedbackField.inputValue(), '保留当前范围，尚未提交的修改意见', 'Saved revision comments must reopen after reload')
        await feedbackField.fill('')
        await approve.waitFor()
        assert.equal(await feedbackField.isVisible(), true, 'Clearing comments must not unexpectedly collapse the editor')
        await comments.focus(); await page.keyboard.press('Enter')
        await input.fill('unsent draft preserved after approval')
        if (process.env.HARNESS_TEST_SCREENSHOTS) {
          await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
          await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/plan-review-' + [language, theme, width].join('-') + '.png'})
        }
        await approve.focus(); await page.keyboard.press('Enter')
        await page.getByText('plan implementation verified', {exact: true}).waitFor()
        await remove.waitFor({state: 'detached'})
        assert.equal(await readOnly.count(), 0, 'Approval must hide the plan hint')
        assert.equal(await input.inputValue(), 'unsent draft preserved after approval')
        await page.waitForFunction(() => !document.querySelector('.composer-editor')?.getAttribute('aria-describedby'))
        if (process.env.HARNESS_TEST_SCREENSHOTS) await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/plan-approved-' + [language, theme, width].join('-') + '.png'})
        await page.reload()
        await page.getByText('plan implementation verified', {exact: true}).waitFor()
        assert.equal(await remove.count(), 0, 'Reloading must not restore a consumed plan selection')
        assert.equal(await input.inputValue(), 'unsent draft preserved after approval')
        assert.equal(await page.locator('html').getAttribute('lang'), language)
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
        assert.ok(sessionId)
        assert.ok((await page.request.post(baseURL + '/api/v1/plan-mode', {data: {session_id: sessionId, active: true}})).ok())
        await readOnly.waitFor()
        let exitRequests = 0
        await page.route('**/api/v1/plan-mode', async route => {
          if (route.request().method() !== 'POST') return route.continue()
          exitRequests++
          await route.fulfill({status: 503, json: {error: 'Mode change temporarily unavailable', code: 'fixture/unavailable'}})
        })
        await remove.focus(); await page.keyboard.press('Enter')
        const feedback = page.getByRole('alert', {name: t('ui.components.chat.Composer.7d5b650bec5e')})
        await feedback.filter({hasText: 'Mode change temporarily unavailable'}).waitFor()
        assert.equal(exitRequests, 1, 'A rejected exit must not be replayed automatically')
        assert.equal(await readOnly.count(), 1, 'A rejected exit must keep the actual plan hint and tag')
        assert.equal(await remove.count(), 1)
        assert.equal(await input.inputValue(), 'unsent draft preserved after approval')
        if (process.env.HARNESS_TEST_SCREENSHOTS) await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/plan-exit-error-' + [language, theme, width].join('-') + '.png'})
        await page.unroute('**/api/v1/plan-mode')
        await remove.focus(); await page.keyboard.press('Enter')
        await remove.waitFor({state: 'detached'})
        assert.equal(await readOnly.count(), 0, 'Removing an active tag must actually exit plan mode')
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
        assert.deepEqual(errors, [])
        await page.close()
        console.log('Plan mode lifecycle passed:', language, theme, width)
      }
    }
  }
} catch (error) {
  console.error(diagnostics)
  if (activePage && !activePage.isClosed()) console.error((await activePage.locator('body').innerText()).slice(-8000))
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
