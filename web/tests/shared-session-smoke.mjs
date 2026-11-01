import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {once} from 'node:events'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {mkdir, readFile} from 'node:fs/promises'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, diagnostics = ''
backend.stderr.on('data', value => {diagnostics += value})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Shared page fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Shared page fixture exited: ' + code)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch { /* 启动日志不是就绪回执。 */ }})
  })
  clearTimeout(timer)
  browser = await launchBrowser()
  const base = 'http://127.0.0.1:' + ready.port
  const errors = []
  for (const language of ['zh-CN', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url)))
    const page = await browser.newPage({viewport: {width, height: 900}})
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(({language, theme}) => {
      localStorage.setItem('harness.language.effective.v1', language)
      localStorage.setItem('harness.ui-theme', theme)
    }, {language, theme})
    assert.ok((await page.request.patch(base + '/api/v1/ui-settings', {data: {language}})).ok())
    await page.goto(base)
    await page.locator('.composer-card').waitFor()
    await page.keyboard.press('ControlOrMeta+Shift+u')
    await page.waitForFunction(() => document.querySelector('.conversation-pane')?.dataset.empty === '1')
    const baseline = await page.locator('.conversation-pane').evaluate(element => {
      const editor = element.querySelector('.composer-editor'), card = element.querySelector('.composer-card')
      return {background: getComputedStyle(element).backgroundColor, headerHeight: element.querySelector('.conversation-tab-header').getBoundingClientRect().height,
        font: getComputedStyle(editor).fontSize, radius: getComputedStyle(card).borderRadius}
    })
    const created = await page.request.post(base + '/api/v1/sessions', {data: {title: 'Shared conversation 原文'}})
    assert.ok(created.ok())
    const session = (await created.json()).session.id
    let contribute = true, attempts = 0, loseResponse = false
    const keys = []
    await page.route('**/api/v1/access/status', route => route.fulfill({json: {
      enabled: true, authenticated: true, subject: 'guest', session_ids: [session], credential_id: 'fixture', read_only: !contribute,
      capabilities: {[session]: contribute ? ['read', 'contribute'] : ['read']},
    }}))
    await page.route('**/api/v1/sessions', route => route.request().method() === 'GET'
      ? route.fulfill({json: {sessions: [{id: session, title: 'Shared conversation 原文'}]}}) : route.continue())
    await page.route(`**/api/v1/sessions/${session}/collaboration`, route => route.fulfill({json: {
      access: {session_id: session, owner: 'owner', revision: 1, members: {guest: contribute ? ['read', 'contribute'] : ['read']}, profiles: {guest: {display_name: 'Guest 原文'}}}, invitations: [],
    }}))
    await page.route(`**/api/v1/sessions/${session}/runs`, route => {
      if (route.request().method() !== 'POST') return route.continue()
      attempts++; keys.push(route.request().headers()['idempotency-key'])
      return loseResponse ? route.abort('failed') : route.continue()
    })
    await page.goto(base + '/shared')
    const pane = page.locator('.conversation-pane'), editor = pane.locator('.composer-editor')
    await editor.waitFor()
    await page.waitForFunction(() => document.querySelector('.conversation-pane')?.dataset.empty === '1')
    assert.equal(await page.locator('#project-sidebar, #task-inspector, .workspace-divider, [data-panel-toggle]').count(), 0)
    assert.equal(await pane.getByRole('tab', {name: 'Shared conversation 原文', exact: true}).count(), 1)
    const sharedStyle = await pane.evaluate(element => ({background: getComputedStyle(element).backgroundColor,
      headerHeight: element.querySelector('.conversation-tab-header').getBoundingClientRect().height,
      font: getComputedStyle(element.querySelector('.composer-editor')).fontSize,
      radius: getComputedStyle(element.querySelector('.composer-card')).borderRadius}))
    assert.deepEqual(sharedStyle, baseline, 'Shared and local conversations must use the same page and editor presentation')
    await editor.fill('Shared message 原文')
    await editor.dispatchEvent('keydown', {key: 'Enter', isComposing: true, keyCode: 229})
    assert.equal(attempts, 0, 'IME confirmation must not submit')
    await editor.press('Shift+Enter')
    assert.ok((await editor.inputValue()).includes('\n'))
    await editor.press('Enter')
    await page.getByText('fixture response verified', {exact: true}).waitFor()
    assert.equal(attempts, 1)
    assert.equal(await editor.inputValue(), '')
    await editor.fill('Preserved shared draft 原文')
    await page.reload()
    await editor.waitFor()
    assert.equal(await editor.inputValue(), 'Preserved shared draft 原文')
    assert.equal(attempts, 1, 'Restoring the page must not resend a draft')
    if (language === 'en' && theme === 'light' && width === 1440) {
      loseResponse = true
      await editor.press('Enter')
      await page.getByRole('button', {name: translations['ui.shared.retrySameRequest'], exact: true}).waitFor()
      assert.equal(attempts, 2)
      await page.reload()
      await page.getByRole('button', {name: translations['ui.shared.retrySameRequest'], exact: true}).waitFor()
      assert.equal(attempts, 2, 'An unknown write must only query its receipt after reload')
      loseResponse = false
      await page.getByRole('button', {name: translations['ui.shared.retrySameRequest'], exact: true}).click()
      await page.waitForFunction(() => document.querySelector('.composer-editor')?.value === '')
      assert.equal(attempts, 3)
      assert.equal(keys[1], keys[2], 'Explicit retry must retain the original idempotency key')
    }
    const hostLanguage = language === 'en' ? 'zh-CN' : 'en'
    const hostTranslations = JSON.parse(await readFile(new URL('../src/i18n/locales/' + hostLanguage + '.json', import.meta.url)))
    const refused = 'access/denied'
    await page.route(`**/api/v1/sessions/${session}/queue`, route => route.fulfill({status: 403, json: {
      code: refused, error: hostTranslations['ui.collaboration.error.' + refused].replace('{code}', refused),
    }}))
    await page.reload()
    const alert = page.getByRole('alert')
    await alert.waitFor()
    assert.equal(await alert.textContent(), translations['ui.collaboration.error.' + refused].replace('{code}', refused))
    await page.getByRole('button', {name: translations['ui.shared.language'], exact: true}).click()
    await page.waitForFunction(expected => document.querySelector('[role="alert"]')?.textContent === expected,
      hostTranslations['ui.collaboration.error.' + refused].replace('{code}', refused))
    assert.equal((await (await page.request.get(base + '/api/v1/ui-settings')).json()).settings.language, language,
      'Viewer language must not change the host configuration')
    await page.getByRole('button', {name: hostTranslations['ui.shared.language'], exact: true}).click()
    await page.waitForFunction(expected => document.querySelector('[role="alert"]')?.textContent === expected,
      translations['ui.collaboration.error.' + refused].replace('{code}', refused))
    await page.unroute(`**/api/v1/sessions/${session}/queue`)
    await page.reload()
    await editor.waitFor()
    if (process.env.HARNESS_TEST_SCREENSHOTS) {
      await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
      await page.screenshot({path: `${process.env.HARNESS_TEST_SCREENSHOTS}/shared-${language}-${theme}-${width}.png`, animations: 'disabled'})
    }
    const geometry = await pane.evaluate(element => ({width: element.getBoundingClientRect().width, scroll: document.documentElement.scrollWidth, viewport: innerWidth}))
    assert.equal(geometry.width, width)
    assert.ok(geometry.scroll <= geometry.viewport + 1, 'Shared conversation must fit the viewport')
    contribute = false
    await page.reload()
    await pane.getByRole('tab', {name: 'Shared conversation 原文', exact: true}).waitFor()
    await page.getByText('fixture response verified', {exact: true}).first().waitFor()
    assert.equal(await editor.count(), 0, 'Observers must not receive an editable input')
    assert.equal(await page.locator('#project-sidebar, #task-inspector').count(), 0)
    await page.close()
  }
  assert.deepEqual(errors, [])
  console.log('Shared conversation: common page/editor, no sidebars, bilingual themes and widths, IME, send, scoped drafts, observer permissions, viewer-language errors and unknown-write receipt recovery passed')
} finally {
  clearTimeout(timer)
  try {await browser?.close()} finally {
    lines.close()
    if (backend.exitCode === null && backend.signalCode === null) {
      const exited = once(backend, 'exit')
      backend.kill('SIGTERM')
      const deadline = setTimeout(() => backend.kill('SIGKILL'), 20000)
      await exited.finally(() => clearTimeout(deadline))
    }
  }
}
