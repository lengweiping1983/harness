import {chooseFixtureProject} from './conversation-project.mjs'
import {cleanupFixture, diagnoseFailure, withDeadline} from './fixture-cleanup.mjs'
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
      const memoryRequests = []
      page.on('pageerror', error => errors.push(error.message))
      page.on('request', request => {if (request.url().includes('/memory')) memoryRequests.push({method: request.method(), url: request.url()})})
      page.on('requestfailed', request => {if (request.url().includes('/memory')) memoryRequests.push({failed: request.failure(), url: request.url()})})
      await page.addInitScript(theme => localStorage.setItem('harness.ui-theme', theme), theme)
      await page.goto(origin)
      await chooseFixtureProject(page)
      const composer = page.locator('.composer-editor')
      await composer.fill('memory fixture ' + [language, theme, width].join('/'))
      await composer.press('Enter')
      await page.getByText('fixture response verified', {exact: true}).first().waitFor()
      await composer.evaluate(element => element.blur())
      await page.keyboard.press('Control+,')
      const agentsGroup = page.getByRole('button', {name: translations['ui.settings.groups.agents'], exact: true})
      if (await agentsGroup.getAttribute('aria-expanded') === 'false') await agentsGroup.click()
      await page.getByRole('button', {name: translations['ui.memory.heading'], exact: true}).click()
      const panel = page.getByRole('region', {name: translations['ui.memory.heading'], exact: true})
      const selector = panel.getByRole('combobox', {name: translations['ui.memory.scope']})
      for (const scope of ['project', 'user', 'session']) {
        await selector.selectOption(scope)
        await panel.getByText(translations['ui.memory.empty'], {exact: true}).waitFor().catch(error => diagnoseFailure(error, [async () => console.error({language, theme, width, scope, text: await panel.innerText()}), async () => {await mkdir(root + '/output/memory-research', {recursive: true}); await page.screenshot({path: root + '/output/memory-research/memory-failure.png'})}]))
        const input = panel.getByRole('textbox', {name: translations['ui.memory.content'], exact: true})
        await input.focus()
        assert.equal(await input.evaluate(element => element === document.activeElement), true)
        const note = 'verified-' + scope
        await input.fill(note)
        await panel.getByRole('switch').click()
        const saving = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/memory?scope=' + scope))
        await panel.getByRole('button', {name: translations['ui.memory.save'], exact: true}).click()
        const saved = await saving
        assert.ok(saved.ok())
        const savedRow = (await saved.json()).memory
        await panel.getByText(note, {exact: true}).waitFor()
        const previewResponse = await page.request.get(saved.url().split('?')[0] + '/preview')
        assert.ok(previewResponse.ok())
        const preview = await previewResponse.json()
        assert.equal(typeof preview.source_seq, 'number')
        assert.ok(preview.used_chars <= preview.budget)
        const selectedLayer = preview.layers.find(layer => layer.scope === scope)
        assert.ok(selectedLayer.recall.selected.some(entry => entry.id === savedRow.id && entry.revision === savedRow.revision && entry.reason === 'pinned'))
        assert.equal(JSON.stringify(preview).includes(note), false)
        await panel.getByRole('button', {name: translations['ui.memory.delete'], exact: true}).click()
        const confirmation = panel.getByRole('group', {name: translations['ui.memory.deleteConfirm'], exact: true})
        await confirmation.waitFor()
        await panel.getByText(note, {exact: true}).waitFor()
        const [deleted] = await Promise.all([
          page.waitForResponse(response => response.request().method() === 'DELETE' && response.url().includes('/memory/' + savedRow.id)),
          confirmation.getByRole('button', {name: translations['ui.memory.confirmDelete'], exact: true}).click(),
        ]).catch(error => diagnoseFailure(error, [async () => console.error({language, theme, width, scope, savedRow, memoryRequests, errors, text: await panel.innerText()}), async () => {await mkdir(root + '/output/memory-research', {recursive: true}); await page.screenshot({path: root + '/output/memory-research/delete-failure.png'})}]))
        assert.ok(deleted.ok())
        await panel.getByText(translations['ui.memory.empty'], {exact: true}).waitFor().catch(error => diagnoseFailure(error, [async () => console.error({language, theme, width, scope, text: await panel.innerText()}), async () => {await mkdir(root + '/output/memory-research', {recursive: true}); await page.screenshot({path: root + '/output/memory-research/memory-failure.png'})}]))
      }
      if (language === 'en' && theme === 'dark' && width === 1440) {
        const pattern = '**/api/v1/sessions/*/memory?scope=user'
        await page.route(pattern, route => route.fulfill({status: 503, json: {code: 'fixture/unavailable', message: 'fixture memory unavailable'}}))
        await selector.selectOption('user')
        await panel.getByRole('alert').filter({hasText: 'fixture memory unavailable'}).waitFor()
        await page.unroute(pattern)
        await selector.selectOption('project')
        await panel.getByText(translations['ui.memory.empty'], {exact: true}).waitFor()
        let release, pending
        const entered = new Promise(resolve => {release = resolve})
        await page.route(pattern, route => {pending = route; release()})
        await selector.selectOption('user')
        await withDeadline(()=>entered,10000,'Memory scope read entered')
        await panel.getByText(translations['ui.memory.loading'], {exact: true}).waitFor()
        assert.equal(await panel.getByText(translations['ui.memory.empty'], {exact: true}).count(), 0)
        const aborted=page.waitForEvent('requestfailed',{predicate:request=>request.url().includes('/memory?scope=user')})
        await selector.selectOption('session')
        await aborted
        await panel.getByText(translations['ui.memory.empty'], {exact: true}).waitFor()
        await pending.fulfill({json: {memories: [{id: 'stale', content: 'stale-layer-result', topic: 'fixture', pinned: false, source: '', evidence: '', revision: 1}], path: 'stale-layer-path'}})
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
        assert.equal(await panel.getByText('stale-layer-result', {exact: true}).count(), 0)
        assert.equal(await panel.getByText('stale-layer-path', {exact: true}).count(), 0)
        assert.equal(await selector.inputValue(), 'session')
        await page.unroute(pattern)
      }
      assert.ok(await panel.evaluate(element => element.scrollWidth <= element.clientWidth + 1))
      assert.deepEqual(errors, [])
      await mkdir(root + '/output/memory-research/screenshots', {recursive: true})
      await page.locator('#settings-content').evaluate(element => {element.scrollTop = 0})
      await page.screenshot({path: root + '/output/memory-research/screenshots/' + [language, theme, width].join('-') + '.png'})
      await page.close()
    }
  }
  console.log('Layered memory: real HTTP CRUD, Chinese/English, light/dark, narrow/wide and focus passed')
} catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
