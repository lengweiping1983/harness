import {chooseFixtureProject} from './conversation-project.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {readFile, mkdir} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-c', "from tests.web_fixture import main; main(enabled=('enable_project_memory',))"], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, failure, diagnostics = ''
backend.stderr.on('data', value => {diagnostics += value})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Fixture exited: ' + code + diagnostics)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  browser = await launchBrowser()
  const origin = 'http://127.0.0.1:' + ready.port
  for (const language of ['zh-CN', 'en']) {
    const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url)))
    const api = await browser.newContext()
    assert.ok((await api.request.patch(origin + '/api/v1/ui-settings', {data: {language}})).ok())
    await api.close()
    for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
      const page = await browser.newPage({viewport: {width, height: 900}})
      const errors = []
      let requests = 0
      page.on('pageerror', error => errors.push(error.message))
      page.on('request', request => {if (request.url().endsWith('/memory/preview')) requests++})
      await page.addInitScript(theme => localStorage.setItem('harness.ui-theme', theme), theme)
      await page.goto(origin)
      await chooseFixtureProject(page)
      await page.locator('.composer-editor').fill('preview fixture')
      await page.locator('.composer-editor').press('Enter')
      await page.getByText('fixture response verified', {exact: true}).first().waitFor()
      await page.locator('.composer-editor').evaluate(element => element.blur())
      await page.keyboard.press('Control+,')
      const agentsGroup = page.getByRole('button', {name: translations['ui.settings.groups.agents'], exact: true})
      if (await agentsGroup.getAttribute('aria-expanded') === 'false') await agentsGroup.click()
      await page.getByRole('button', {name: translations['ui.memory.heading'], exact: true}).click()
      const panel = page.getByRole('region', {name: translations['ui.memory.heading'], exact: true})
      const input = panel.getByRole('textbox', {name: translations['ui.memory.content'], exact: true})
      await input.fill('private-preview-body')
      await panel.getByRole('switch').click()
      const savedResponse = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/memory?scope=project'))
      await panel.getByRole('button', {name: translations['ui.memory.save'], exact: true}).click()
      const saved = await savedResponse
      assert.ok(saved.ok())
      const row = (await saved.json()).memory
      await panel.getByText('private-preview-body', {exact: true}).waitFor()
      assert.equal(requests, 0)
      const show = panel.getByRole('button', {name: translations['ui.memory.preview.show'], exact: true})
      const response = page.waitForResponse(value => value.url().endsWith('/memory/preview'))
      await show.focus()
      await page.keyboard.press('Enter')
      const report = await (await response).json()
      const preview = panel.getByRole('region', {name: translations['ui.memory.preview.title'], exact: true})
      const selected = preview.getByText(translations['ui.memory.preview.selected'], {exact: true})
      await selected.waitFor()
      await selected.click()
      assert.ok((await preview.innerText()).includes(row.id))
      assert.equal((await preview.innerText()).includes('private-preview-body'), false)
      assert.ok(report.layers.some(layer => layer.recall?.selected.some(item => item.id === row.id)))
      assert.ok(await preview.evaluate(element => element.scrollWidth <= element.clientWidth + 1))
      await mkdir(root + '/output/memory-research/preview-ui-screenshots', {recursive: true})
      await preview.scrollIntoViewIfNeeded()
      await page.screenshot({path: root + '/output/memory-research/preview-ui-screenshots/' + [language, theme, width].join('-') + '.png'})
      if (language === 'en' && theme === 'dark' && width === 1440) {
        const pattern = '**/api/v1/sessions/*/memory/preview'
        const explanation = structuredClone(report)
        const project = explanation.layers.find(layer => layer.scope === 'project').recall
        project.total += 2
        project.selected[0].source_status = 'changed'
        project.selected[0].derivation_status = 'changed'
        project.skipped_budget.push({id: 'mem_' + 'd'.repeat(32), revision: 3, reason: 'pinned', source_status: 'unchecked', derivation_status: 'unchecked'})
        project.suppressed.push({id: 'mem_' + 'e'.repeat(32), revision: 2, summary_id: row.id, summary_revision: row.revision})
        await page.route(pattern, route => route.fulfill({json: explanation}))
        await preview.getByRole('button', {name: translations['ui.memory.preview.refresh'], exact: true}).click()
        await preview.getByText(translations['ui.memory.preview.skipped'], {exact: true}).click()
        await preview.getByText(translations['ui.memory.preview.covered'], {exact: true}).click()
        assert.ok((await preview.innerText()).includes('mem_' + 'd'.repeat(32)))
        assert.ok((await preview.innerText()).includes('mem_' + 'e'.repeat(32)))
        await page.unroute(pattern)
        await page.route(pattern, route => route.fulfill({status: 503, json: {message: 'preview fixture unavailable', code: 'fixture/unavailable'}}))
        await preview.getByRole('button', {name: translations['ui.memory.preview.refresh'], exact: true}).click()
        await preview.getByRole('alert').filter({hasText: 'preview fixture unavailable'}).waitFor()
        await page.unroute(pattern)
        await preview.getByRole('button', {name: translations['ui.memory.preview.refresh'], exact: true}).click()
        await selected.waitFor()
        let pending, release
        const entered = new Promise(resolve => {release = resolve})
        await page.route(pattern, route => {pending = route; release()})
        await preview.getByRole('button', {name: translations['ui.memory.preview.refresh'], exact: true}).click()
        await entered
        await panel.getByRole('button', {name: translations['ui.memory.preview.hide'], exact: true}).click()
        await pending.fulfill({json: report})
        await page.unroute(pattern)
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
        assert.equal(await preview.count(), 0)
        await show.click()
        await selected.waitFor()
        const scopeEntered = new Promise(resolve => {release = resolve})
        await page.route(pattern, route => {pending = route; release()})
        await preview.getByRole('button', {name: translations['ui.memory.preview.refresh'], exact: true}).click()
        await scopeEntered
        await panel.getByRole('combobox', {name: translations['ui.memory.scope']}).selectOption('session')
        await pending.fulfill({json: report})
        await page.unroute(pattern)
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
        assert.equal(await preview.count(), 0)
        assert.equal(await show.getAttribute('aria-expanded'), 'false')
      } else {
        await panel.getByRole('button', {name: translations['ui.memory.preview.hide'], exact: true}).click()
        assert.equal(await preview.count(), 0)
      }
      assert.deepEqual(errors, [])
      assert.ok((await page.request.delete(saved.url().split('?')[0] + '/' + row.id, {data: {expected_revision: row.revision}})).ok())
      await page.close()
    }
  }
  console.log('Recall preview UI: eight layouts, private metadata, keyboard, on-demand reads, error recovery, close and scope isolation passed')
} catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
