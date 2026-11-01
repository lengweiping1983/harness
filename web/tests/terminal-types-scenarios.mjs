import assert from 'node:assert/strict'
import {mkdir, readFile} from 'node:fs/promises'

export async function verifyTerminalTypes(browser, baseURL) {
  const english = JSON.parse(await readFile(new URL('../src/i18n/locales/en.json', import.meta.url), 'utf8'))
  const page = await browser.newPage({viewport: {width: 1440, height: 900}})
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const available = {backends: ['cmd', 'powershell', 'pwsh'], default_type: 'cmd', enabled: true, managed_runtime: false}
  let response, receiveHeldRoute, requests = 0
  function holdResponse() {
    response = 'hold'
    return new Promise(resolve => {receiveHeldRoute = resolve})
  }
  await page.route('**/api/v1/terminal/types', async route => {
    requests++
    if (response === 'hold') {receiveHeldRoute(route); return}
    await route.fulfill(response)
  })
  const home = page.locator('#inspector-tab-content[data-panel="home"]')
  async function showHome() {
    await page.getByRole('button', {name: /^(启动器|Launcher)$/}).click()
    await home.waitFor({state: 'visible'})
  }
  async function fits(label) {
    assert.ok(await home.evaluate(element => element.scrollWidth <= element.clientWidth + 1), label)
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), label)
  }
  async function screenshot(name) {
    if (!process.env.HARNESS_TEST_SCREENSHOTS) return
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await page.screenshot({animations: 'disabled', path: process.env.HARNESS_TEST_SCREENSHOTS + '/terminal-' + name + '.png'})
  }
  try {
    response = {json: available}
    await page.goto(baseURL)
    // 首屏初始化会持久化当前主题；就绪后再设置下一次重载的偏好。
    await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
    for (const language of ['zh-CN', 'en']) {
      assert.ok((await page.request.patch(baseURL + '/api/v1/ui-settings', {data: {language}})).ok())
      for (const theme of ['light', 'dark']) {
        for (const width of [390, 1440]) {
          const zh = language === 'zh-CN'
          const label = [language, theme, width].join('-')
          await page.setViewportSize({width, height: 900})
          await page.evaluate(({theme}) => {
            localStorage.setItem('harness.ui-theme', theme)
            localStorage.setItem('harness.inspector-tab.v1', 'home')
            localStorage.setItem('harness.terminal-type.v1', 'removed-shell')
          }, {theme})
          const heldRoute = holdResponse()
          const catalogRequest = page.waitForRequest('**/api/v1/terminal/types')
          await page.reload()
          await showHome()
          await catalogRequest
          assert.equal(await page.locator('html').getAttribute('lang'), language)
          assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
          await home.getByRole('status').filter({hasText: zh ? '加载终端类型' : 'Loading terminal types'}).waitFor()
          assert.equal(await home.getByRole('combobox').count(), 0, 'Loading must not show a meaningless dropdown')
          await screenshot('loading-' + label)
          response = {json: available}
          await (await heldRoute).fulfill(response)
          const select = home.getByRole('combobox', {name: zh ? '新终端类型' : 'New terminal type', exact: true})
          await select.waitFor()
          assert.deepEqual(await select.locator('option').allTextContents(), available.backends)
          assert.equal(await select.inputValue(), 'cmd', 'A removed preference must use the server default')
          await select.focus()
          assert.ok(await select.evaluate(element => element === document.activeElement))
          await select.selectOption('powershell')
          assert.equal(await page.evaluate(() => localStorage.getItem('harness.terminal-type.v1')), 'powershell')
          await fits('Available terminal types must fit ' + label)
          await screenshot('ready-' + label)

          response = {status: 503, json: {error: 'Terminal catalog temporarily unavailable', code: 'fixture/unavailable'}}
          await page.reload(); await showHome()
          await home.getByRole('alert').filter({hasText: 'Terminal catalog temporarily unavailable'}).waitFor()
          assert.equal(await home.getByRole('combobox').count(), 0)
          await fits('Terminal errors must fit ' + label)
          await screenshot('error-' + label)
          const retry = home.getByRole('button', {name: zh ? '重新加载终端类型' : 'Reload terminal types', exact: true})
          response = {json: {...available, enabled: false}}
          await retry.focus(); await page.keyboard.press('Enter')
          await home.getByRole('status').filter({hasText: zh ? '未启用终端' : 'Terminal capabilities are not enabled'}).waitFor()
          assert.equal(await home.getByRole('combobox').count(), 0)
          response = {json: {...available, backends: []}}
          await retry.click()
          await home.getByRole('status').filter({hasText: zh ? '没有可用的终端类型' : 'No terminal types available'}).waitFor()
          await fits('Empty terminal types must fit ' + label)
          await screenshot('empty-' + label)
          response = {json: available}
          await retry.click()
          await select.waitFor()
          assert.equal(await select.inputValue(), 'powershell', 'Reload and retry must preserve a valid preference')

          const beforeHide = requests
          await page.keyboard.press('Control+j')
          assert.equal(await page.locator('#task-inspector').getAttribute('aria-hidden'), 'true')
          assert.equal(requests, beforeHide, 'Hiding the inspector must not fetch terminal types')
          response = {json: {...available, backends: ['powershell'], default_type: 'powershell'}}
          await page.keyboard.press('Control+j')
          await page.waitForFunction(() => {
            const select = document.querySelector('#inspector-tab-content select')
            return select?.value === 'powershell' && select.options.length === 1
          })
          assert.deepEqual(await select.locator('option').allTextContents(), ['powershell'], 'Reopening must refresh the catalog')
        }
      }
    }
    response = {json: {}}
    await page.reload(); await showHome()
    await home.getByRole('alert').waitFor()
    assert.equal(await home.getByRole('combobox').count(), 0, 'Malformed success responses must expose an error')
    const heldStaleRoute = holdResponse()
    const staleRequest = page.waitForRequest('**/api/v1/terminal/types')
    await home.getByRole('button', {name: 'Reload terminal types', exact: true}).click()
    await staleRequest
    const staleRoute = await heldStaleRoute
    await page.keyboard.press('Control+j')
    response = {json: {...available, backends: ['pwsh'], default_type: 'removed-default'}}
    await page.keyboard.press('Control+j')
    await page.waitForFunction(() => document.querySelector('#inspector-tab-content select')?.value === 'pwsh')
    await staleRoute.fulfill({json: {...available, backends: ['cmd'], default_type: 'cmd'}})
    assert.deepEqual(await home.getByRole('combobox').locator('option').allTextContents(), ['pwsh'], 'A response from before hiding must not replace the refreshed catalog')
    response = {json: available}
    await page.evaluate(() => localStorage.setItem('harness.terminal-type.v1', 'cmd'))
    await page.reload(); await showHome()
    const select = home.getByRole('combobox', {name: 'New terminal type', exact: true})
    await select.waitFor()
    const terminal = {sessionId: 'fixture-terminal', type: 'cmd', cwd: 'fixture', pid: 1, status: {kind: 'running'}, transport: 'pipe', output_error:{key:'ui.terminal.outputFailed',params:{error:'Original {diagnostic}'}}}
    const opened = []
    await page.route('**/api/v1/sessions/*/terminals', route => {
      if (route.request().method() === 'GET') return route.fulfill({json: {...available, terminals: [terminal]}})
      opened.push(route.request().postDataJSON())
      return route.fulfill({status: 409, json: {error: 'Fixture does not start a shell', code: 'fixture/no-spawn'}})
    })
    await page.route('**/terminals/fixture-terminal/stream?*', route => route.fulfill({json: {text: '', cursor: 0, start: 0, end: 0, reset: false, eof: true, terminal}}))
    await home.getByRole('button', {name: 'Terminal', exact: true}).click()
    await page.locator('#inspector-tab-content[data-panel="terminal"] [data-terminal-id="fixture-terminal"]').waitFor()
    await page.getByRole('tab', {name: 'cmd · Terminal 1', exact: true}).waitFor()
    assert.equal(await page.getByRole('tab', {name: /fixture-terminal/}).count(), 0)
    await page.getByRole('alert').filter({hasText:english['ui.terminal.outputFailed'].replace('{error}','Original {diagnostic}')}).waitFor()
    await page.locator('#task-inspector').getByRole('button', {name: english['ui.workspace.launcher.open'], exact: true}).click()
    await select.waitFor()
    await select.selectOption('pwsh')
    const openRequest = page.waitForRequest(request => request.url().endsWith('/terminals') && request.method() === 'POST')
    await home.getByRole('button', {name: 'Terminal', exact: true}).click()
    await openRequest
    assert.deepEqual(opened, [{type: 'pwsh'}], 'A retained terminal workbench must use the newly selected type immediately')
    assert.deepEqual(errors, [])
    console.log('Terminal types: loading, defaults, selection, failure/retry, disabled, empty, visibility, immediate creation; 8 language/theme/width combinations passed')
  } finally {
    try {assert.ok((await page.request.patch(baseURL + '/api/v1/ui-settings', {data: {language: 'zh-CN'}})).ok())}
    finally {await page.close()}
  }
}
