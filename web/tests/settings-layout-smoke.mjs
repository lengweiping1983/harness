import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, diagnostics = ''
backend.stderr.on('data', value => {diagnostics += value})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(new Error('Fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(new Error('Fixture exited: ' + code)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  browser = await launchBrowser()
  const page = await browser.newPage({viewport: {width: 390, height: 900}})
  await page.goto('http://127.0.0.1:' + ready.port)
  await page.locator('textarea').first().waitFor()
  await page.keyboard.press('Control+,')
  const basicPages = page.locator('nav button[data-page]:visible')
  await basicPages.first().waitFor({state: 'visible'})
  assert.deepEqual(await basicPages.evaluateAll(nodes=>nodes.map(node=>node.dataset.page)), ['general','models','permissions','shortcuts','voice','personality','agents','skills','tools','memory','connectors','plugins','mcpServer','hooks','automations'])
  await basicPages.last().focus()
  await page.keyboard.press('ArrowDown')
  assert.equal(await basicPages.first().evaluate(el => el === document.activeElement), true)
  assert.equal(await page.locator('nav button[aria-expanded="true"]').count(), 4)
  for (const language of ['en', 'zh-CN', 'en']) {
    const {readFileSync} = await import('node:fs')
    const copy = JSON.parse(readFileSync(new URL('../../harness/i18n/locales/' + language + '.json', import.meta.url), 'utf8'))
    const content = page.locator('#settings-content')
    await content.locator('#language-preference').selectOption(language)
    await content.getByText(copy['ui.components.overlays.SettingsDialog.c92eca635bc8'], {exact: true}).waitFor()
    const advancedGroup = page.getByRole('button', {name: copy['ui.settings.groups.advanced'], exact: true})
    if (await advancedGroup.getAttribute('aria-expanded') === 'false') await advancedGroup.click()
    await page.getByRole('button', {name: copy['ui.components.overlays.SettingsDialog.926b04e8ced4'], exact: true}).waitFor()
    assert.equal(await content.locator('[data-settings-save-hint]').count(), 0)
    const slider = content.getByRole('slider', {name: copy['ui.components.overlays.SettingsDialog.6089721beb10'], exact: true})
    assert.equal(await slider.getAttribute('min'), '10')
    assert.equal(await slider.getAttribute('max'), '22')
    assert.equal(await slider.getAttribute('step'), '1')
    const connection = content.getByText(copy['ui.components.overlays.SettingsDialog.6dceff73f657'], {exact: true})
    await connection.waitFor()
    assert.equal(await connection.locator('svg').count(), 0)
    for (const width of [390, 1280]) {
      await page.setViewportSize({width, height: 900})
      for (const theme of ['aa0819dfc4d8', 'a6b75d068032', '217cfe7db1e3']) {
        const button = content.getByRole('button', {name: copy['ui.components.overlays.SettingsDialog.' + theme], exact: true})
        await button.click()
        assert.equal(await button.getAttribute('aria-pressed'), 'true')
        assert.equal(await content.locator('[data-settings-save-hint]').count(), 0)
        const description = content.getByText(copy['ui.components.overlays.SettingsDialog.c92eca635bc8'], {exact: true})
        assert.ok(await description.evaluate(el => el.parentElement.parentElement.classList.contains('border')))
        for (const option of await content.locator('button[aria-pressed]').all()) {
          const style = await option.evaluate(el => {const s = getComputedStyle(el); return {border: s.borderTopWidth, height: el.getBoundingClientRect().height, width: el.getBoundingClientRect().width, right: el.getBoundingClientRect().right}})
          assert.equal(style.border, '1px')
          assert.ok(style.height >= 32 && style.width > 0 && style.right <= width)
        }
        if (language === 'zh-CN' && theme === 'a6b75d068032') await page.screenshot({path: root + 'output/settings-layout-' + width + '.png'})
        await button.focus()
        assert.equal(await button.evaluate(el => el === document.activeElement), true)
        assert.equal(await content.evaluate(el => el.scrollWidth <= el.clientWidth), true)
      }
    }
  }
  await page.setViewportSize({width: 390, height: 640})
  await contentFontSize(page, 22)
  for (const group of ['basic', 'agents', 'connections', 'advanced']) {
    const button = page.locator('nav button[aria-controls="settings-group-' + group + '"]')
    if (await button.getAttribute('aria-expanded') === 'false') await button.click()
  }
  assert.ok(await page.locator('#settings-content').evaluate(el => el.getBoundingClientRect().height > 150))
  assert.ok(await page.getByRole('dialog').locator('nav').evaluate(el => el.scrollHeight > el.clientHeight))
  console.log('Settings layout: language round trip, themes, mobile/desktop, card ownership, removed global hints and keyboard focus passed')
} finally {
  clearTimeout(timer)
  if (browser) await browser.close()
  lines.close()
  if (backend.exitCode === null) {
    const exited = new Promise(resolve => backend.once('exit', resolve))
    backend.kill('SIGTERM')
    await exited
  }
}

async function contentFontSize(page, size) {
  await page.evaluate(size => document.documentElement.style.fontSize = size + 'px', size)
}
