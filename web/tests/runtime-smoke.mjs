import {chooseFixtureProject} from './conversation-project.mjs'
import {cleanupFixture, diagnoseFailure} from './fixture-cleanup.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {mkdir} from 'node:fs/promises'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
let diagnostics = '', browser, page, failure
backend.stderr.on('data', data => {diagnostics += data})
const lines = createInterface({input: backend.stdout})
let startupTimer
try {
  const ready = await new Promise((resolve, reject) => {
    startupTimer = setTimeout(() => reject(new Error('Harness fixture startup timed out\n' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(new Error('Harness fixture exited: ' + code + '\n' + diagnostics)))
    lines.on('line', line => {
      try {const value = JSON.parse(line); if (Number.isInteger(value.port)) resolve(value)} catch { /* 启动诊断不是就绪回执。 */ }
    })
  })
  clearTimeout(startupTimer)
  browser = await launchBrowser()
  page = await browser.newPage({viewport: {width: 1440, height: 900}})
  async function revealContextSettings() {
    const disclosure = page.locator('[data-context-settings]')
    await disclosure.waitFor({state: 'visible'})
    const composition = page.locator('[data-context-composition]')
    assert.equal(await composition.count(), 0, 'Input details must not be rendered')
    const usageSummary = page.getByRole('region', {name: /^(用量汇总|Usage summary)$/})
    await usageSummary.waitFor({state: 'visible'})
    assert.ok(await usageSummary.evaluate(element => !element.closest('details')), 'Usage summary must be visible before any disclosure')
    assert.equal(await usageSummary.count(), 1, 'The original statistics must appear only once')
    assert.equal(await usageSummary.locator('svg circle').count(), 2, 'The original cache ring must remain available')
    assert.equal(await usageSummary.locator('ul > li').count(), 4, 'All original token breakdown rows must remain available')
    assert.ok(await usageSummary.evaluate(element => {
      const panel = element.parentElement
      const action = panel.querySelector('button:not([aria-label])')
      return action && Boolean(element.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING)
    }), 'Statistics must precede the manual context actions')
    assert.ok(await usageSummary.evaluate(element => element.scrollWidth <= element.clientWidth + 1), 'Statistics must fit the panel width')
    if (await disclosure.getAttribute('open') === null) {
      if (process.env.HARNESS_TEST_SCREENSHOTS) {
        await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
        const language = await page.locator('html').getAttribute('lang')
        const theme = await page.locator('html').getAttribute('data-theme')
        await page.screenshot({animations: 'disabled', path: process.env.HARNESS_TEST_SCREENSHOTS + '/review-' + language + '-' + theme + '-' + page.viewportSize().width + '.png'})
      }
      await disclosure.locator(':scope > summary').focus()
      await page.keyboard.press('Enter')
    }
  }
  const errors = []
  let contextWrites = 0, sessionWrites = 0
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => {
    const path = new URL(request.url()).pathname
    if (path === '/api/v1/settings' && request.method() === 'PATCH') contextWrites++
    if (path === '/api/v1/sessions' && request.method() === 'POST') sessionWrites++
  })
  await page.goto('http://127.0.0.1:' + ready.port)
  const input = page.getByRole('textbox', {name: '输入消息', exact: true})
  await input.fill('browser fixture request')
  await input.press('Enter')
  await page.getByRole('dialog', {name: '选择项目', exact: true}).waitFor()
  assert.equal(sessionWrites, 0, 'Missing project selection must not create a persistent conversation')
  assert.equal(await input.inputValue(), 'browser fixture request', 'Project selection preserves the unsent message')
  await chooseFixtureProject(page)
  await input.fill('browser fixture request')
  await input.press('Enter')
  await page.getByText('fixture response verified', {exact: true}).waitFor()
  assert.equal(sessionWrites, 1, 'The checked project permits exactly one conversation creation')
  assert.equal(await page.getByRole('button', {name: '发送', exact: true}).isDisabled(), true)
  assert.equal(await input.inputValue(), '')
  const user = page.getByRole('article').filter({hasText: 'browser fixture request'})
  await user.getByRole('button', {name: '编辑消息'}).waitFor()
  for (const width of [390, 1440]) {
    await page.setViewportSize({width, height: 900})
    await input.waitFor({state: 'visible'})
    const bounds = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}))
    assert.ok(bounds.scroll <= bounds.width, 'The conversation must fit the viewport at ' + width)
  }
  await page.reload()
  await page.getByText('fixture response verified', {exact: true}).waitFor()
  assert.equal(await page.getByText('fixture response verified', {exact: true}).count(), 1)
  for (const language of ['zh-CN', 'en']) {
    const saved = await page.request.patch('http://127.0.0.1:' + ready.port + '/api/v1/ui-settings', {data: {language}})
    assert.ok(saved.ok(), 'The isolated server must accept the language preference')
    for (const theme of ['light', 'dark']) {
      for (const width of [390, 1440]) {
        await page.setViewportSize({width, height: 900})
        await page.evaluate(value => {
          localStorage.setItem('harness.ui-theme', value)
          localStorage.setItem('harness.inspector-tab.v1', 'home')
        }, theme)
        await page.reload()
        await page.getByText('fixture response verified', {exact: true}).waitFor()
        assert.equal(await page.locator('html').getAttribute('lang'), language)
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
        await page.keyboard.press('Meta+j')
        const home = page.locator('#inspector-tab-content[data-panel="home"]')
        await home.waitFor({state: 'visible'})
        await page.keyboard.press('Control+Alt+j')
        assert.equal(await page.locator('#task-inspector').getAttribute('aria-hidden'), 'false', 'AltGr-style input must not toggle the workspace')
        await page.keyboard.press('Control+j')
        await page.waitForFunction(() => document.getElementById('task-inspector')?.getAttribute('aria-hidden') === 'true')
        await page.keyboard.press('Control+j')
        await page.waitForFunction(() => document.getElementById('task-inspector')?.getAttribute('aria-hidden') === 'false')
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
        const newTabLabel = language === 'zh-CN' ? '启动器' : 'Launcher'
        await page.locator('#task-inspector').getByRole('tab', {name: newTabLabel, exact: true}).waitFor()
        assert.equal(await home.getByRole('heading').count(), 5, 'Launcher exposes five consistent groups')
        const colors = await page.evaluate(() => {
          const background = selector => getComputedStyle(document.querySelector(selector)).backgroundColor
          return {
            selected: background('.inspector-tab-header .browser-tab[data-active="true"]'),
            switcher: background('.inspector-section-switcher'),
            field: background('.inspector-section-switcher select'),
            content: background('#inspector-tab-content'),
          }
        })
        assert.equal(colors.switcher, colors.selected)
        assert.equal(colors.field, colors.selected)
        assert.equal(colors.content, colors.selected)
        const common = home.getByRole('navigation', {name: language === 'zh-CN' ? '文件、预览与终端' : 'Files, preview and terminal', exact: true})
        const launchButtons = common.locator(':scope > div > button')
        assert.equal(await launchButtons.count(), 3)
        const help = home.locator('summary').filter({hasText: language === 'zh-CN' ? '学习与帮助' : 'Learning and help'}).locator('..')
        await help.getByRole('button', {name: language === 'zh-CN' ? '学习中心' : 'Learning', exact: true}).waitFor({state: 'visible'})
        const terminalLabel = launchButtons.last().locator('span').last()
        const labelFits = await terminalLabel.evaluate(element => {
          const lineHeight = Number.parseFloat(getComputedStyle(element).lineHeight)
          return element.getBoundingClientRect().height <= lineHeight + 1
        })
        assert.ok(labelFits, 'The terminal label must remain readable on one line')
        const details = home.locator('summary').filter({hasText: language === 'zh-CN' ? '执行详情' : 'Execution details'}).locator('..')
        assert.notEqual(await details.getAttribute('open'), null, 'Launcher groups start expanded')
        const summary = details.locator('summary')
        await summary.focus()
        await page.keyboard.press('Enter')
        assert.equal(await details.getAttribute('open'), null)
        await page.keyboard.press('Enter')
        assert.notEqual(await details.getAttribute('open'), null)
        const diagnosticLinks = details.getByRole('navigation').getByRole('button')
        assert.equal(await diagnosticLinks.count(), 3)
        await diagnosticLinks.last().click()
        await page.locator('#inspector-tab-content[data-panel="prompts"]').waitFor({state: 'visible'})
        const bounds = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}))
        assert.ok(bounds.scroll <= bounds.width, 'The workspace must fit ' + [language, theme, width].join('/'))
        await page.keyboard.press('Control+3')
        await revealContextSettings()
        const settings = page.getByRole('region', {name: language === 'zh-CN' ? '上下文管理' : 'Context management', exact: true})
        await settings.getByRole('switch').waitFor({state: 'visible'})
        const advanced = settings.locator('details')
        assert.equal(await advanced.getAttribute('open'), null)
        const writesBeforeChoice = contextWrites
        const balanced = settings.locator('button[aria-pressed]').nth(1)
        await balanced.click()
        assert.equal(await balanced.getAttribute('aria-pressed'), 'true')
        assert.equal(contextWrites, writesBeforeChoice, 'Choosing a preset must only edit the draft')
        if (process.env.HARNESS_TEST_SCREENSHOTS) {
          await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
          await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/context-' + [language, theme, width].join('-') + '.png'})
        }
        assert.equal(await settings.getByRole('combobox').isVisible(), false, 'Technical settings must be disclosed through advanced options')
        await advanced.locator('summary').focus()
        await page.keyboard.press('Enter')
        await settings.getByRole('combobox').waitFor({state: 'visible'})
        const contextFields=['tool_result_max_chars','summary_max_tokens','output_reserve','context_window']
        const contextCopy=await import('node:fs/promises').then(async({readFile})=>JSON.parse(await readFile(new URL('../../harness/i18n/locales/'+language+'.json',import.meta.url),'utf8')))
        for(const field of contextFields)await advanced.getByRole('spinbutton',{name:contextCopy['ui.context.'+field],exact:true}).waitFor()
        assert.equal(await advanced.getByRole('spinbutton').count(), contextFields.length)
        assert.equal(await advanced.getByRole('spinbutton',{name:contextCopy['ui.context.context_window'],exact:true}).inputValue(), '')
        assert.equal(await settings.getByRole('combobox').inputValue(), 'auto')
        const contextBounds = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}))
        assert.ok(contextBounds.scroll <= contextBounds.width, 'Context settings must fit ' + [language, theme, width].join('/'))
        if (language === 'en' && theme === 'dark' && width === 1440) {
          const saved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/settings' && response.request().method() === 'PATCH')
          await settings.getByRole('button', {name: 'Save', exact: true}).click()
          assert.ok((await saved).ok())
          await settings.getByText('Saved and verified', {exact: true}).waitFor({state: 'visible'})
          const persisted = await page.request.get('http://127.0.0.1:' + ready.port + '/api/v1/settings')
          assert.equal((await persisted.json()).config.context_working_budget, 256000)
          await settings.getByRole('switch').click()
          assert.equal(await settings.getByText('Saved and verified', {exact: true}).count(), 0, 'Editing must invalidate the previous save confirmation')
          await settings.locator('[data-save-phase="unsaved"]').getByText('Unsaved', {exact: true}).waitFor({state: 'visible'})
          const unchanged = await page.request.get('http://127.0.0.1:' + ready.port + '/api/v1/settings')
          assert.equal((await unchanged.json()).config.context_auto_manage, true, 'Editing must not silently save')
        }
        await page.keyboard.press('Control+1')
        const webSettings = page.getByRole('region', {name: language === 'zh-CN' ? '网页搜索与抓取' : 'Web search and fetch', exact: true})
        await webSettings.getByRole('switch').first().waitFor({state: 'visible'})
        assert.equal(await webSettings.getByRole('switch').count(), 2)
        assert.equal(await webSettings.locator('input[type="password"]').count(), 2)
        const order = webSettings.locator('details')
        assert.equal(await order.getAttribute('open'), null)
        if (process.env.HARNESS_TEST_SCREENSHOTS) {
          await page.screenshot({animations: 'disabled', path: process.env.HARNESS_TEST_SCREENSHOTS + '/web-settings-default-' + [language, theme, width].join('-') + '.png'})
        }
        await order.locator('summary').focus()
        await page.keyboard.press('Enter')
        await webSettings.locator('details[open]').waitFor({state: 'visible'})
        assert.ok(await webSettings.evaluate(element => element.scrollWidth <= element.clientWidth + 1))
        if (process.env.HARNESS_TEST_SCREENSHOTS) {
          await page.screenshot({animations: 'disabled', path: process.env.HARNESS_TEST_SCREENSHOTS + '/web-settings-' + [language, theme, width].join('-') + '.png'})
        }
        if (language === 'en' && theme === 'dark' && width === 1440) {
          await webSettings.getByRole('button', {name: 'Save web settings', exact: true}).click()
          await webSettings.getByText('Saved; applies to subsequent tool calls', {exact: true}).waitFor({state: 'visible'})
          await webSettings.locator('input[type="password"]').first().fill('unsaved-test-input')
          assert.equal(await webSettings.getByText('Saved; applies to subsequent tool calls', {exact: true}).count(), 0, 'Changing a key must invalidate the previous save confirmation')
        }
        await page.keyboard.press('Control+,')
      }
    }
  }
  await page.evaluate(() => {
    window.__nativeLayoutCalls = []
    const shortcut = event => {
      if (event.code !== 'Backquote') return
      window.removeEventListener('keydown', shortcut)
      queueMicrotask(() => {window.__nativeShortcut = {key: event.key, code: event.code,
        ctrl: event.ctrlKey, shift: event.shiftKey, meta: event.metaKey, alt: event.altKey,
        composing: event.isComposing, keyCode: event.keyCode, prevented: event.defaultPrevented,
        modal: Boolean(document.querySelector('[aria-modal="true"]'))}})
    }
    window.addEventListener('keydown', shortcut)
    const subscribe = () => () => {}
    window.harnessDesktop = {
      onServiceState: subscribe,
      onBrowserState: subscribe, onFocusFind: subscribe, onFocusAddress: subscribe, onBrowserError: subscribe,
      browser: async (action, args) => {
        if (action === 'service_state') return {status: 'running'}
        if (action === 'layout') window.__nativeLayoutCalls.push(args)
        return {session_id: args.session_id, active_tab: null, tabs: []}
      },
    }
  })
  // 注入桥接后触发外壳重渲染，确保服务状态订阅也经过验收。
  await page.keyboard.press('Control+b')
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await page.locator('.composer-editor').waitFor({state: 'visible'})
  assert.deepEqual(errors, [], 'The desktop bridge fixture must support shell rerenders')
  await page.keyboard.press('Control+Shift+Backquote')
  await page.locator('#native-browser-view').waitFor({state: 'visible'})
  await page.waitForFunction(() => window.__nativeLayoutCalls.some(call => call.visible))
  await page.keyboard.press('Control+3')
        await revealContextSettings()
  await page.locator('#inspector-tab-content[data-panel="context"]').waitFor({state: 'visible'})
  const hiddenObservation = await page.evaluate(async () => {
    const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    await settle()
    const before = window.__nativeLayoutCalls.length
    const probe = document.createElement('div')
    probe.setAttribute('aria-hidden', 'true')
    document.body.append(probe)
    await settle()
    const after = window.__nativeLayoutCalls.length
    probe.remove()
    return {before, after}
  })
  assert.equal(hiddenObservation.after, hiddenObservation.before, 'Hidden native browser must suspend layout observation')
  await page.keyboard.press('Control+Shift+Backquote')
  await page.waitForFunction(() => window.__nativeLayoutCalls.at(-1)?.visible === true)
  await page.route('**/api/v1/settings', route => route.fulfill({json: {}}))
  await page.keyboard.press('Control+3')
        await revealContextSettings()
  const invalidSettings = page.getByRole('alert').filter({hasText: 'The service returned an invalid response.'})
  await invalidSettings.waitFor({state: 'visible'})
  assert.equal(await page.getByText('Loading settings…', {exact: true}).count(), 0, 'Context load errors must end the loading announcement')
  await page.unroute('**/api/v1/settings')
  await page.keyboard.press('Control+Shift+Backquote')
  await page.locator('#inspector-tab-content[data-panel="browser"]').waitFor({state: 'visible'})
  await page.keyboard.press('Control+3')
        await revealContextSettings()
  await page.getByRole('switch', {name: 'Automatic management', exact: true}).waitFor({state: 'visible'})
  assert.equal(await invalidSettings.count(), 0)
  await page.evaluate(() => {delete window.harnessDesktop})
  await page.route('**/api/v1/ui-settings', route => route.fulfill({json: {}}))
  await page.keyboard.press('Control+1')
  const webSettingsError = page.getByRole('region', {name: 'Web search and fetch', exact: true})
  await webSettingsError.getByRole('alert').waitFor({state: 'visible'})
  assert.equal(await webSettingsError.getByText('Loading web settings…', {exact: true}).count(), 0, 'A failed load must not keep announcing loading')
  assert.equal(await webSettingsError.getByRole('switch').count(), 0)
  await page.unroute('**/api/v1/ui-settings')
  await page.keyboard.press('Control+,')
  await page.keyboard.press('Control+1')
  await page.getByRole('region', {name: 'Web search and fetch', exact: true}).getByRole('switch').first().waitFor({state: 'visible'})
  await page.keyboard.press('Control+,')
  let memoryReads = 0, pendingMemoryWrite
  let acceptMemoryWrite
  const memoryWriteEntered = new Promise(resolve => {acceptMemoryWrite = resolve})
  await page.route('**/api/v1/sessions/*/memory?scope=project', async route => {
    if (route.request().method() === 'GET') {
      memoryReads++
      await route.fulfill({json: {memories: [], path: 'fixture-memory'}})
    } else {
      pendingMemoryWrite = route
      acceptMemoryWrite()
    }
  })
  await page.keyboard.press('Control+,')
  const agentsGroup = page.getByRole('button', {name: 'Agents and capabilities', exact: true})
  if (await agentsGroup.getAttribute('aria-expanded') === 'false') await agentsGroup.click()
  await page.getByRole('button', {name: 'Layered memory', exact: true}).click()
  const memory = page.getByRole('region', {name: 'Layered memory', exact: true})
  await memory.getByRole('textbox', {name: 'Memory content', exact: true}).fill('temporary memory draft')
  await memory.getByRole('button', {name: 'Save memory', exact: true}).click()
  await memoryWriteEntered
  await page.keyboard.press('Control+,')
  const readsBeforeReply = memoryReads
  const memoryWriteResponse = page.waitForResponse(response => response.url() === pendingMemoryWrite.request().url() && response.request().method() === pendingMemoryWrite.request().method())
  await pendingMemoryWrite.fulfill({json: {}})
  await (await memoryWriteResponse).finished()
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  assert.equal(memoryReads, readsBeforeReply, 'Closing a memory page must stop follow-up reads from its pending write')
  let pendingExport, acceptExport, downloads = 0
  const exportEntered = new Promise(resolve => {acceptExport = resolve})
  page.on('download', () => {downloads++})
  await page.route('**/api/v1/sessions/*/memory/export?scope=project', route => {pendingExport = route; acceptExport()})
  await page.keyboard.press('Control+,')
  await memory.getByRole('button', {name: 'Export JSON', exact: true}).waitFor({state: 'visible'})
  await memory.getByRole('button', {name: 'Export JSON', exact: true}).click()
  await exportEntered
  await page.keyboard.press('Control+,')
  const memoryExportResponse = page.waitForResponse(response => response.url() === pendingExport.request().url() && response.request().method() === pendingExport.request().method())
  await pendingExport.fulfill({json: {memories: []}})
  await (await memoryExportResponse).finished()
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  assert.equal(downloads, 0, 'A closed memory page must not initiate a late download')
  await page.unroute('**/api/v1/sessions/*/memory/export?scope=project')
  await page.unroute('**/api/v1/sessions/*/memory?scope=project')
  await page.setViewportSize({width: 390, height: 900})
  for (const language of ['zh-CN', 'en']) {
    const saved = await page.request.patch('http://127.0.0.1:' + ready.port + '/api/v1/ui-settings', {data: {language}})
    assert.ok(saved.ok())
    for (const fontSize of [10, 22]) {
      await page.evaluate(({fontSize, language}) => {
        localStorage.setItem('harness.ui-font-size', String(fontSize))
        localStorage.setItem('harness.ui-theme', language === 'zh-CN' ? 'light' : 'dark')
      }, {fontSize, language})
      await page.reload()
      await page.getByText('fixture response verified', {exact: true}).waitFor()
      assert.equal(await page.locator('html').evaluate(element => getComputedStyle(element).fontSize), fontSize + 'px')
      await page.keyboard.press('Control+3')
        await revealContextSettings()
      const settings = page.getByRole('region', {name: language === 'zh-CN' ? '上下文管理' : 'Context management', exact: true})
      await settings.getByRole('switch').waitFor({state: 'visible'})
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
      await settings.locator('summary').focus()
      await page.keyboard.press('Enter')
      await settings.getByRole('combobox').waitFor({state: 'visible'})
      await settings.getByRole('spinbutton').last().focus()
      assert.ok(await settings.getByRole('spinbutton').last().evaluate(element => document.activeElement === element))
      const fits = await settings.evaluate(element => element.scrollWidth <= element.clientWidth + 1)
      assert.ok(fits, 'Context fields must fit at font size ' + fontSize + '/' + language)
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      if (process.env.HARNESS_TEST_SCREENSHOTS) {
        await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/context-font-' + fontSize + '-' + language + '.png'})
      }
    }
  }
  const projectionResponse = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/context/compact') && response.request().method() === 'POST')
  await page.getByRole('button', {name: 'Shorten tool results', exact: true}).click()
  const projection = await projectionResponse
  assert.equal(projection.status(), 202)
  assert.equal(projection.request().postDataJSON().mode, 'project')
  await page.waitForFunction(async url => {
    const response = await fetch(url)
    const run = await response.json()
    return run?.status === 'completed'
  }, projection.url(), {polling: 100, timeout: 10000})
  const projected = await page.request.get(projection.url())
  const projectionRun = await projected.json()
  assert.equal(projectionRun.result.mode, 'project')
  assert.equal(typeof projectionRun.result.before_tokens, 'number')
  assert.equal(typeof projectionRun.result.after_tokens, 'number')
  assert.deepEqual(errors, [])
  console.log('Runtime browser: conversation, launcher, context settings and 10/22 font boundaries passed')
} catch (error) {
  failure = error
  await diagnoseFailure(error, [async () => {
    if (page && !page.isClosed()) console.error('Runtime failure state:', await page.evaluate(() => ({
    shortcut: window.__nativeShortcut, native: Boolean(window.harnessDesktop),
    panel: document.querySelector('#inspector-tab-content')?.getAttribute('data-panel'),
    inspectorHidden: document.querySelector('#task-inspector')?.getAttribute('aria-hidden'),
    modal: Boolean(document.querySelector('[aria-modal="true"]')),
    visibility: document.visibilityState, focused: document.activeElement?.tagName,
    nativeView: document.querySelector('#native-browser-view')?.getBoundingClientRect().toJSON(),
  })))
  }]).catch(original => {failure = original})
} finally {
  clearTimeout(startupTimer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
