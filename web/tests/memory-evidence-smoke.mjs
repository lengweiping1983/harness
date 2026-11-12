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
      page.on('pageerror', error => errors.push(error.message))
      await page.addInitScript(theme => localStorage.setItem('harness.ui-theme', theme), theme)
      await page.goto(origin)
      await chooseFixtureProject(page)
      await page.locator('.composer-editor').fill('evidence fixture')
      await page.locator('.composer-editor').press('Enter')
      await page.getByText('fixture response verified', {exact: true}).first().waitFor()
      await page.locator('.composer-editor').evaluate(element => element.blur())
      await page.keyboard.press('Control+,')
      const agentsGroup = page.getByRole('button', {name: translations['ui.settings.groups.agents'], exact: true})
      if (await agentsGroup.getAttribute('aria-expanded') === 'false') await agentsGroup.click()
      await page.getByRole('button', {name: translations['ui.memory.heading'], exact: true}).click()
      const panel = page.getByRole('region', {name: translations['ui.memory.heading'], exact: true})
      const savedResponse = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/memory?scope=project'))
      await panel.getByRole('textbox', {name: translations['ui.memory.content'], exact: true}).fill('source evidence fixture')
      await panel.getByRole('button', {name: translations['ui.memory.save'], exact: true}).click()
      const response = await savedResponse
      assert.ok(response.ok())
      const row = (await response.json()).memory
      const base = response.url().split('?')[0]
      const prefix = 'harness:file-sources:v1\n'
      const source = {note: 'original source note', files: [{path: 'src/队列.py', version: '0123456789abcdef'}]}
      assert.ok((await page.request.patch(base + '/' + row.id, {data: {...row, expected_revision: row.revision, evidence: prefix + JSON.stringify(source)}})).ok())
      await panel.getByRole('button', {name: translations['ui.memory.refresh'], exact: true}).click()
      await panel.getByText(source.note, {exact: true}).waitFor()
      const article = panel.locator('article').filter({hasText: 'source evidence fixture'})
      await article.getByText(translations['ui.memory.fileSources'], {exact: true}).waitFor()
      assert.equal(await article.locator('details').evaluate(element => element.open), false)
      const summary = article.locator('summary')
      await summary.focus()
      await page.keyboard.press('Enter')
      assert.equal(await article.locator('details').evaluate(element => element.open), true)
      await article.getByRole('button', {name: translations['ui.memory.edit'], exact: true}).click()
      const evidenceInput = panel.getByRole('textbox', {name: translations['ui.memory.evidence'], exact: true})
      assert.equal(await evidenceInput.inputValue(), source.note)
      await evidenceInput.fill('x'.repeat(1000))
      assert.equal(await panel.getByRole('button', {name: translations['ui.memory.update'], exact: true}).isDisabled(), true)
      await evidenceInput.fill('edited source note')
      const patched = page.waitForResponse(value => value.request().method() === 'PATCH' && value.url().includes('/memory/' + row.id))
      await panel.getByRole('button', {name: translations['ui.memory.update'], exact: true}).click()
      const updated = (await (await patched).json()).memory
      assert.deepEqual(JSON.parse(updated.evidence.slice(prefix.length)), {...source, note: 'edited source note'})
      await panel.getByText('edited source note', {exact: true}).waitFor()
      assert.ok(await panel.evaluate(element => element.scrollWidth <= element.clientWidth + 1))
      assert.deepEqual(errors, [])
      await mkdir(root + '/output/memory-research/evidence-screenshots', {recursive: true})
      await article.scrollIntoViewIfNeeded()
      await page.screenshot({path: root + '/output/memory-research/evidence-screenshots/' + [language, theme, width].join('-') + '.png'})
      const derivationPrefix = 'harness:consolidation:v1\n'
      const derivation = {request: 'a'.repeat(64), note: 'consolidation note', inputs: [
        {id: 'mem_' + 'b'.repeat(32), input_revision: 1, revision: 2, pinned: true},
        {id: 'mem_' + 'c'.repeat(32), input_revision: 3, revision: 3, pinned: false},
      ]}
      assert.ok((await page.request.patch(base + '/' + row.id, {data: {...updated, expected_revision: updated.revision, evidence: derivationPrefix + JSON.stringify(derivation)}})).ok())
      await panel.getByRole('button', {name: translations['ui.memory.refresh'], exact: true}).click()
      await article.getByText(translations['ui.memory.consolidationInputs'], {exact: true}).waitFor()
      await article.getByRole('button', {name: translations['ui.memory.edit'], exact: true}).click()
      assert.equal(await evidenceInput.inputValue(), derivation.note)
      await evidenceInput.fill('edited consolidation note')
      const consolidatedResponse = page.waitForResponse(value => value.request().method() === 'PATCH' && value.url().includes('/memory/' + row.id))
      await panel.getByRole('button', {name: translations['ui.memory.update'], exact: true}).click()
      const consolidated = (await (await consolidatedResponse).json()).memory
      assert.deepEqual(JSON.parse(consolidated.evidence.slice(derivationPrefix.length)), {...derivation, note: 'edited consolidation note'})
      assert.deepEqual(errors, [])
      assert.ok((await page.request.delete(base + '/' + row.id, {data: {expected_revision: consolidated.revision}})).ok())
      await page.close()
    }
  }
  console.log('Memory evidence: readable sources, disclosure keyboard, note edit references, budget recovery and eight display variants passed')
} catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
