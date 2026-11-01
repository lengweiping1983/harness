import assert from 'node:assert/strict'
import {cleanupFixture} from './fixture-cleanup.mjs'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, failure, diagnostics = ''
backend.stderr.on('data', data => {diagnostics += data})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Fixture exited: ' + code + '\n' + diagnostics)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  browser = await launchBrowser()
  const url = 'http://127.0.0.1:' + ready.port
  for (const width of [390, 1440, 390, 1440]) {
    const page = await browser.newPage({viewport: {width, height: 900}})
    await page.addInitScript(() => {
      localStorage.setItem('harness.inspector-tab.v1', 'context')
    })
    await page.goto(url)
    const input = page.getByRole('textbox', {name: '输入消息', exact: true})
    await input.waitFor()
    await page.keyboard.press('Control+3')
    const selected = page.locator('#task-inspector [role="tab"][aria-selected="true"]')
    await page.keyboard.press('Control+2')
    await page.locator('#inspector-tab-content[data-panel="todos"]').waitFor({state: 'visible'})
    await selected.focus()
    const tabsBeforeComposition = await page.locator('#task-inspector [role="tab"]').count()
    for (const key of ['Home', 'ArrowLeft', 'End', 'ArrowRight', 'Delete']) {
      for (const composition of [{isComposing: true}, {keyCode: 229}]) {
        await selected.dispatchEvent('keydown', {key, bubbles: true, cancelable: true, ...composition})
        await page.evaluate(() => new Promise(requestAnimationFrame))
        assert.equal(await page.locator('#inspector-tab-content').getAttribute('data-panel'), 'todos',
          'Input method candidate keys must not switch inspector tabs')
        assert.equal(await page.locator('#task-inspector [role="tab"]').count(), tabsBeforeComposition,
          'Input method candidate keys must not close inspector tabs')
        assert.ok(await selected.evaluate(element => document.activeElement === element),
          'Input method candidate keys must retain the original tab focus')
      }
    }
    await selected.press('Home')
    await page.locator('#inspector-tab-content[data-panel="context"]').waitFor({state: 'visible'})
    assert.deepEqual(await page.locator('#task-inspector [role="tab"]').evaluateAll(tabs => tabs.map(tab => tab.getAttribute('data-tab-value'))), ['context', 'todos'],
      'Rapid Home navigation must retain the Todos tab')
    assert.ok(await selected.evaluate(element => document.activeElement === element), 'Home must focus the Context tab')
    await selected.press('End')
    await page.locator('#inspector-tab-content[data-panel="todos"]').waitFor({state: 'visible'})
    assert.ok(await selected.evaluate(element => document.activeElement === element), 'Keyboard navigation must focus the selected tab')
    await selected.press('Delete')
    await page.locator('#inspector-tab-content[data-panel="context"]').waitFor({state: 'visible'})
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    assert.ok(await selected.evaluate(element => document.activeElement === element),
      'Closing a selected tab must focus its surviving neighbour')
    await selected.focus()
    await selected.press('Delete')
    await page.waitForFunction(() => document.getElementById('task-inspector')?.getAttribute('aria-hidden') === 'true')
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    assert.ok(await page.evaluate(() => Boolean(document.activeElement?.closest('.conversation-tab-header'))),
      'Closing the last inspector tab must restore focus to the conversation')
    await page.close()
  }
  console.log('Inspector keyboard: Home/End navigation, neighbour focus and final-tab conversation focus at narrow/wide widths')

 } catch (error) {
  failure = error
} finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
