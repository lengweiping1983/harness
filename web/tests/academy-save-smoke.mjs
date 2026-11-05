import {openAcademySection} from './academy-sections.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {fixturePython} from './python-fixture.mjs'
import {launchBrowser} from './browser-scenario.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'
import {interceptSaveReceipt} from './academy-save-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.academy_web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, page, failure, timer, diagnostics = ''
const releases = []
backend.stderr.on('data', data => {diagnostics += data})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Save ownership fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Save ownership fixture exited: ' + code + diagnostics)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (Number.isInteger(value.port)) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  const base = 'http://127.0.0.1:' + ready.port
  browser = await launchBrowser()
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}})
  page = await context.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(base)
  await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  await page.keyboard.press('Control+j')
  const helpGroup = page.locator('#inspector-tab-content[data-panel="home"] details').filter({hasText: '学习与帮助'})
  if ((await helpGroup.getAttribute('open')) === null) await helpGroup.locator('summary').click()
  await page.locator('#inspector-tab-content[data-panel="home"]').getByRole('button', {name: '学习中心', exact: true}).click()
  const panel = page.locator('#inspector-tab-content[data-panel="academy"]')
  await panel.locator('[data-course-id="harness-foundations"]').getByRole('button').click()
  const editor = panel.getByRole('textbox', {name: '练习代码', exact: true})
  await editor.waitFor()
  const get = async path => (await page.request.get(base + '/api/v1/academy' + path)).json()
  const project = (await get('/projects')).projects[0]
  const refreshSource = async (action, expected) => {
    const refreshed = page.waitForRequest(request => request.method() === 'GET'
      && request.url() === base + '/api/v1/academy/projects/' + project.id + '/source?path=main.py').then(async request => {
      const response = await request.response()
      assert.ok(response?.ok(), 'The editor must finish its new source read')
      assert.equal((await response.json()).content, expected)
    })
    await Promise.all([refreshed, Promise.resolve().then(action)])
    await page.waitForFunction(expected => document.querySelector('textarea[aria-label="练习代码"]')?.value === expected
      && document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false, expected)
  }
  const initial = await editor.inputValue()
  const saved = initial + '\n# 第一个窗口保存 / first saved version\n'
  const newer = initial + '\n# 第二个窗口的新草稿 / newer other-window draft\n'
  const draftKey = 'harness.academy.draft.v1:' + JSON.stringify([project.id, 'main.py'])
  const endpoint = '**/api/v1/academy/projects/' + project.id + '/files'
  let releaseSave, sawSave, handledSave, writes = 0
  const gate = new Promise(resolve => {releaseSave = resolve})
  const observed = new Promise(resolve => {sawSave = resolve})
  const handled = new Promise(resolve => {handledSave = resolve})
  releases.push(releaseSave)
  page.on('request', request => {if (request.method() === 'POST' && request.url().endsWith('/files')) writes++})
  await page.route(endpoint, async route => {
    const response = await route.fetch()
    sawSave()
    await gate
    try {await route.fulfill({response})} catch (error) {if (!route.request().failure()) throw error} finally {handledSave()}
  })
  await editor.fill(saved)
  await openAcademySection(panel, 'code-options')
  await panel.getByRole('button', {name: '保存修改', exact: true}).click()
  await observed
  const other = await page.context().newPage()
  other.on('pageerror', error => errors.push(error.message))
  await other.goto(base)
  await other.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  if (await other.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await other.keyboard.press('Control+j')
  const home = other.locator('#inspector-tab-content[data-panel="home"]')
  if (await home.count()) {
    const group = home.locator('summary').filter({hasText: '学习与帮助'})
    if (await group.locator('..').getAttribute('open') === null) await group.click()
    await home.getByRole('button', {name: '学习中心', exact: true}).click()
  }
  const otherPanel = other.locator('#inspector-tab-content[data-panel="academy"]')
  const otherEditor = otherPanel.getByRole('textbox', {name: '练习代码', exact: true})
  await otherEditor.waitFor()
  await otherEditor.fill(newer)
  const newerBytes = await other.evaluate(key => localStorage.getItem(key), draftKey)
  assert.equal(JSON.parse(newerBytes).content, newer)
  releaseSave(); await handled
  await page.waitForFunction(() => sessionStorage.getItem('harness.academy.pending.v1') === null)
  assert.equal(await page.evaluate(key => localStorage.getItem(key), draftKey), newerBytes, 'A completed manual save must not remove another window’s newer draft')
  assert.equal((await get('/projects/' + project.id + '/source?path=main.py')).content, saved)
  assert.equal(await otherEditor.inputValue(), newer)
  assert.equal(writes, 1)
  await page.unroute(endpoint)
  await other.close()
  await page.waitForFunction(expected => document.querySelector('textarea[aria-label="练习代码"]')?.value === expected, newer)
  await openAcademySection(panel, 'code-options')
  await panel.getByRole('button', {name: '保存修改', exact: true}).click()
  await panel.getByText('文件已经变化，请重新读取并核对修改。', {exact: true}).waitFor()
  assert.equal(await page.evaluate(key => localStorage.getItem(key), draftKey), newerBytes)
  assert.equal((await get('/projects/' + project.id + '/source?path=main.py')).content, saved)
  await refreshSource(() => panel.getByRole('button', {name: '放弃未保存修改', exact: true}).click(), saved)
  const recover = async expected => {
    const pending = JSON.parse(await page.evaluate(() => sessionStorage.getItem('harness.academy.pending.v1')))
    assert.deepEqual(pending.sourceInput, expected)
    await page.unroute(endpoint)
    await page.reload()
    await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
    if (await page.locator('#task-inspector').getAttribute('aria-hidden') === 'true') await page.keyboard.press('Control+j')
    await editor.waitFor()
    const current = await get('/projects/' + project.id + '/source?path=main.py')
    await refreshSource(() => panel.getByRole('button', {name: '回查操作回执', exact: true}).click(), current.content)
    await page.waitForFunction(() => sessionStorage.getItem('harness.academy.pending.v1') === null)
    return pending
  }
  for (const field of ['path', 'content']) {
    await page.waitForFunction(() => document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
    const version = (await get('/projects/' + project.id + '/source?path=main.py')).version
    const candidate = saved + '# receipt ownership / 回执身份 ' + field + '\n'
    await editor.fill(candidate)
    await openAcademySection(panel, 'code-options')
    await interceptSaveReceipt(page, endpoint, source => {
      source[field] = field === 'path' ? 'foreign.py' : 'FOREIGN_RESPONSE'
    }, async () => {
      const listing = await page.evaluate(url => fetch(url).then(response => response.json()),
        base + '/api/v1/academy/projects/' + project.id + '/files')
      assert.deepEqual(listing.files.map(file => file.path), ['main.py'])
      await panel.getByRole('button', {name: '保存修改', exact: true}).click()
    })
    await panel.getByText('服务返回了无法解析的响应，请重试。', {exact: true}).waitFor()
    const beforeRecovery = writes
    await recover({path: 'main.py', content: candidate, expected_version: version})
    await page.waitForFunction(expected => document.querySelector('textarea[aria-label="练习代码"]')?.value === expected, candidate)
    assert.equal(writes, beforeRecovery, 'Receipt recovery must not repeat a save')
    assert.equal((await get('/projects/' + project.id + '/source?path=main.py')).content, candidate)
    assert.equal(await page.evaluate(key => localStorage.getItem(key), draftKey), null)
  }
  await page.waitForFunction(() => document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
  await panel.locator('summary').filter({hasText: '资料与历史'}).click()
  await panel.getByRole('textbox', {name: '新文件路径', exact: true}).fill('notes.py')
  await interceptSaveReceipt(page, endpoint, source => {source.path = 'foreign-new.py'},
    () => panel.getByRole('button', {name: '新建辅助文件', exact: true}).click())
  await panel.getByText('服务返回了无法解析的响应，请重试。', {exact: true}).waitFor()
  const beforeRecovery = writes
  await recover({path: 'notes.py', content: '', expected_version: 'absent'})
  assert.equal(writes, beforeRecovery)
  assert.equal((await get('/projects/' + project.id + '/source?path=notes.py')).content, '')
  assert.equal((await get('/projects/' + project.id + '/files')).files.some(file => file.path === 'foreign-new.py' || file.path === 'foreign.py'), false)
  assert.equal(writes, 5)
  await assert.rejects(interceptSaveReceipt(page, endpoint, assert.fail, async () => {
    const failed = await page.evaluate(async url => {
      try {
        await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({request_id: crypto.randomUUID(), path: 'notes.py', content: 'invalid request must not write'})})
        return false
      } catch {return true}
    }, base + '/api/v1/academy/projects/' + project.id + '/files')
    assert.equal(failed, true)
  }), error => error.name === 'AssertionError' && error.message.includes('HTTP 400'))
  assert.equal(writes, 6, 'An invalid request must fail once without killing the worker or replaying a save')
  assert.equal((await get('/projects/' + project.id + '/source?path=notes.py')).content, '')
  assert.deepEqual((await get('/projects/' + project.id + '/runs')).runs, [])
  assert.deepEqual(errors, [])
  console.log('Academy save: two-window newer drafts, stale-version rejection, exact save/new-file receipt identity, reload recovery, HTTP error interception, no execution and no replay verified')
} catch (error) {failure = error}
finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend, releases})
}
