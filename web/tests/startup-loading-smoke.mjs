import {chooseFixtureProject} from './conversation-project.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'
import {cleanupFixture, withDeadline} from './fixture-cleanup.mjs'

const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {
  cwd: fileURLToPath(new URL('../../', import.meta.url)), stdio: ['ignore', 'pipe', 'pipe'],
})
const lines = createInterface({input: backend.stdout})
let browser, error, diagnostics = ''
const releases = []
backend.stderr.on('data', value => {diagnostics += value})
try {
  const {port} = await withDeadline(() => new Promise((resolve, reject) => {
    backend.once('error', reject)
    backend.once('exit', code => reject(Error(`Backend exited: ${code}\n${diagnostics}`)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch {}})
  }), 30000, 'Backend startup')
  browser = await launchBrowser()
  for (const width of [390, 1440]) {
    const page = await browser.newPage({viewport: {width, height: 900}})
    const errors = []
    page.on('pageerror', error => errors.push(String(error)))
    await page.goto(`http://127.0.0.1:${port}`)
    const input = page.getByRole('textbox', {name: '输入消息', exact: true})
    await input.waitFor()
    await page.waitForLoadState('networkidle')
    const entries = await page.evaluate(() => performance.getEntriesByType('resource').map(resource => resource.toJSON()))
    assert.ok(!entries.some(entry => /\/(MessageList|MemberDetailPanel|MarkdownContent|CommandPalette|ConversationSearch|QuickOpen|WorkspacePicker|ImageLightbox|katex|mermaid|xterm)-/.test(entry.name)), 'Unused renderers and overlays must stay off the empty first screen')
    const app = entries.find(entry => /\/App-[^/]+\.js$/.test(entry.name))
    const locale = entries.find(entry => /\/zh-CN-[^/]+\.js$/.test(entry.name))
    assert.ok(app && locale && app.startTime < locale.responseEnd, 'Application code starts before the locale finishes')
    const scripts = entries.filter(entry => entry.name.endsWith('.js'))
    assert.ok(scripts.every(entry => entry.encodedBodySize < entry.decodedBodySize), 'Production JavaScript is compressed on the wire')

    // 设置外壳随主界面到达，首次打开无需额外模块下载。
    let settingsDownloads = 0
    await page.route('**/assets/SettingsDialog-*.js', route => {settingsDownloads++; return route.abort('failed')})
    await input.focus()
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').waitFor({timeout: 2000})
    assert.equal(settingsDownloads, 0)
    await page.keyboard.press('Escape')
    await page.getByRole('dialog').waitFor({state: 'hidden'})
    assert.equal(await input.evaluate(element => element === document.activeElement), true)
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').waitFor({timeout: 2000})
    await page.keyboard.press('Escape')
    let commandRelease, commandEntered
    const commandRequested = new Promise(resolve => {commandEntered = resolve})
    await page.route('**/assets/CommandPalette-*.js', route => {
      commandRelease = () => route.continue()
      releases.push(commandRelease)
      commandEntered()
    })
    await input.focus()
    await page.keyboard.press('Control+k')
    await withDeadline(() => commandRequested, 10000, 'Deferred command palette request')
    await page.getByRole('dialog').getByRole('status').waitFor()
    await page.getByRole('dialog').getByRole('button', {name: '关闭', exact: true}).focus()
    await commandRelease()
    releases.pop()
    await page.unroute('**/assets/CommandPalette-*.js')
    await page.getByRole('dialog').getByRole('combobox').waitFor()
    await page.keyboard.press('Escape')
    assert.equal(await input.evaluate(element => element === document.activeElement), true, 'Loaded overlay restores focus to the original editor')
    if (width === 1440) {
      await chooseFixtureProject(page)
      await input.fill('**startup lazy markdown** $x^2$')
      await input.press('Enter')
      await page.getByText('fixture response verified', {exact: true}).waitFor()
      await page.locator('.md strong').filter({hasText: 'startup lazy markdown'}).waitFor()
      await page.locator('.katex').first().waitFor()
      assert.match(await page.locator('.katex').first().evaluate(element => getComputedStyle(element).fontFamily), /KaTeX_Main/, 'Math styles arrive with the deferred renderer')
      await page.reload()
      await page.getByText('fixture response verified', {exact: true}).waitFor()
      await page.locator('.md strong').filter({hasText: 'startup lazy markdown'}).waitFor()
    }
    assert.deepEqual(errors, [])
    await page.close()
  }
  console.log('Cold startup defers unused UI, overlaps locale and application download, preserves compressed transport, modal cancellation, first opening and Markdown rendering at narrow/wide widths')
} catch (failure) {error = failure}
finally {await cleanupFixture({error, browser, lines, backend, releases})}
