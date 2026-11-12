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
    timer = setTimeout(() => reject(Error('Editor focus fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Editor focus fixture exited: ' + code + diagnostics)))
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
  const content = await editor.inputValue() + '\nprint("FOCUS_OWNER_RESULT")\n'
  await editor.fill(content)
  const endpoint = '**/api/v1/academy/projects/' + project.id + '/run'
  async function delayedRun(action, hidden = false) {
    let entered, release, finished
    const started = new Promise(resolve => {entered = resolve})
    const fulfilled = new Promise(resolve => {finished = resolve})
    let receipt
    await page.route(endpoint, async route => {
      if (route.request().method() !== 'POST') return route.continue()
      const response = await route.fetch()
      receipt = await response.json()
      entered()
      await new Promise(resolve => {release = resolve; releases.push(resolve)})
      await route.fulfill({response})
      finished()
    })
    await page.waitForFunction(() => document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
    await editor.press('Control+Enter')
    await started
    await action()
    release()
    await fulfilled
    await page.unroute(endpoint)
    if (hidden) {
      await page.keyboard.press('Control+j')
      await panel.getByRole('button', {name: '回查操作回执', exact: true}).click()
    }
    await page.waitForFunction(() => !sessionStorage.getItem('harness.academy.pending.v1') && document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
    await panel.locator('[data-academy-run-id="' + receipt.result.run.id + '"]').waitFor()
    return receipt.result.run
  }
  const question = panel.getByRole('textbox', {name: '向带练助手提问', exact: true})
  await delayedRun(async () => {await question.fill('我在这里继续提问，不要抢焦点。')})
  assert.equal(await question.evaluate(node => node === document.activeElement), true)
  assert.equal(await question.inputValue(), '我在这里继续提问，不要抢焦点。')
  await delayedRun(async () => {
    await page.keyboard.press('Control+j')
    assert.equal(await page.locator('#task-inspector').getAttribute('aria-hidden'), 'true')
  }, true)
  assert.equal(await editor.evaluate(node => node === document.activeElement), false, 'Reopening and checking a receipt cannot revive an old keyboard-focus intent')
  const runs = (await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/runs')).json()).runs
  assert.equal(runs.length, 2)
  assert.ok(runs.every(run => run.result.stdout.includes('FOCUS_OWNER_RESULT')))
  assert.equal((await (await page.request.get(base + '/api/v1/academy/projects/' + project.id + '/tutor-turns')).json()).tutor_turns.length, 0)
  assert.deepEqual(errors, [])
  console.log('Academy editor focus: accepted real runs preserve a newer question focus, hidden completion and manual receipt recovery never revive stale focus, no execution replay verified')
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
