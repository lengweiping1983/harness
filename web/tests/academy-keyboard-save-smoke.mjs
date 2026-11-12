import {openAcademySection} from './academy-sections.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile} from 'node:fs/promises'
import {fixturePython} from './python-fixture.mjs'
import {launchBrowser} from './browser-scenario.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.academy_web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, page, failure, timer, diagnostics = ''
const releases = []
backend.stderr.on('data', data => {diagnostics += data})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Keyboard save fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Keyboard save fixture exited: ' + code + diagnostics)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (Number.isInteger(value.port)) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  const base = 'http://127.0.0.1:' + ready.port
  browser = await launchBrowser()
  page = await browser.newPage({viewport: {width: 1440, height: 1000}})
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
  const editor = panel.locator('.academy-workbench-main textarea[spellcheck="false"]')
  await editor.waitFor()
  const get = async path => (await page.request.get(base + '/api/v1/academy' + path)).json()
  const project = (await get('/projects')).projects[0]
  const original = await editor.inputValue()
  let writes = 0
  page.on('request', request => {if (request.method() === 'POST' && request.url().endsWith('/files')) writes++})
  const question = panel.getByRole('textbox', {name: '向带练助手提问', exact: true})
  await question.fill('保留原来的问题 / keep my question')
  assert.equal(await question.evaluate(node => node.dispatchEvent(new KeyboardEvent('keydown', {key: 's', ctrlKey: true, bubbles: true, cancelable: true}))), true)
  const content = original + Array.from({length: 80}, (_, index) => '# save scroll ' + index + '\n').join('')
  await editor.fill(content)
  for (const options of [{ctrlKey: true, isComposing: true}, {metaKey: true, keyCode: 229}, {ctrlKey: true, repeat: true}]) {
    assert.equal(await editor.evaluate((node, options) => !node.dispatchEvent(new KeyboardEvent('keydown', {key: 's', bubbles: true, cancelable: true, ...options})), options), true)
  }
  for (const options of [{ctrlKey: true, shiftKey: true}, {ctrlKey: true, altKey: true}]) {
    assert.equal(await editor.evaluate((node, options) => node.dispatchEvent(new KeyboardEvent('keydown', {key: 's', bubbles: true, cancelable: true, ...options})), options), true)
  }
  await editor.evaluate(node => {
    const event = new KeyboardEvent('keydown', {key: 's', ctrlKey: true, bubbles: true, cancelable: true})
    Object.defineProperty(event, 'getModifierState', {value: key => key === 'AltGraph'})
    node.dispatchEvent(event)
  })
  await editor.dispatchEvent('compositionstart')
  await editor.press('Control+s')
  await editor.dispatchEvent('compositionend')
  assert.equal(writes, 0)
  const save = async (key, expected) => {
    await editor.evaluate(node => {node.focus(); node.setSelectionRange(4, 11, 'backward'); node.scrollTop = 35})
    const selection = await editor.evaluate(node => ({start: node.selectionStart, end: node.selectionEnd, direction: node.selectionDirection, top: node.scrollTop, left: node.scrollLeft}))
    const response = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/files'))
    await editor.press(key)
    const receipt = await (await response).json()
    assert.equal(receipt.status, 'ready')
    await page.waitForFunction(() => sessionStorage.getItem('harness.academy.pending.v1') === null && document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
    await page.waitForFunction(() => document.activeElement === document.querySelector('.academy-workbench-main textarea[spellcheck="false"]'))
    assert.deepEqual(await editor.evaluate(node => ({start: node.selectionStart, end: node.selectionEnd, direction: node.selectionDirection, top: node.scrollTop, left: node.scrollLeft})), selection)
    assert.equal(await editor.inputValue(), expected)
    assert.equal((await get('/projects/' + project.id + '/source?path=main.py')).content, expected)
    assert.equal(await page.evaluate(key => localStorage.getItem(key), 'harness.academy.draft.v1:' + JSON.stringify([project.id, 'main.py'])), null)
  }
  await save('Control+s', content)
  assert.equal(writes, 1)
  await editor.press('Meta+s')
  assert.equal(writes, 1, 'A clean file needs no redundant save request')
  let current = content + '# Meta save\n'
  await editor.fill(current)
  await save('Meta+s', current)
  for (const language of ['en', 'zh-CN', 'en']) {
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled, language)
    await page.keyboard.press('Control+,')
    const copy = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf-8'))
    await page.waitForFunction(() => document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
    current += '# ' + language + '\n'
    await editor.fill(current)
    await save('Control+s', current)
    await openAcademySection(panel, 'code-options')
    assert.equal(await panel.getByRole('button', {name: copy['ui.academy.save'], exact: true}).getAttribute('aria-keyshortcuts'), 'Control+s Meta+s')
    assert.equal(await panel.getByRole('textbox', {name: copy['ui.academy.tutor.question'], exact: true}).inputValue(), '保留原来的问题 / keep my question')
  }
  const endpoint = '**/api/v1/academy/projects/' + project.id + '/files'
  await page.route(endpoint, async route => {await route.fetch(); await route.abort('failed')})
  current += '# unconfirmed save\n'
  await editor.fill(current)
  const lost = page.waitForEvent('requestfailed', request => request.method() === 'POST' && request.url().endsWith('/files'))
  await editor.press('Control+s')
  await lost
  const beforeRecovery = writes
  await page.unroute(endpoint)
  const finalQuestion = panel.getByRole('textbox', {name: 'Ask about this lesson', exact: true})
  await finalQuestion.fill('A newer question focus / 新问题')
  await panel.getByRole('button', {name: 'Check operation receipt', exact: true}).click()
  await page.waitForFunction(() => sessionStorage.getItem('harness.academy.pending.v1') === null && document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled === false)
  assert.equal(await editor.evaluate(node => node === document.activeElement), false, 'Manual recovery must not revive the old keyboard focus intent')
  assert.equal(await finalQuestion.inputValue(), 'A newer question focus / 新问题')
  assert.equal(writes, beforeRecovery)
  assert.equal((await get('/projects/' + project.id + '/source?path=main.py')).content, current)
  assert.deepEqual((await get('/projects/' + project.id + '/runs')).runs, [])
  assert.deepEqual((await get('/projects/' + project.id + '/tutor-turns')).tutor_turns, [])
  assert.deepEqual(errors, [])
  console.log('Academy keyboard save: actual Ctrl/Meta saves without execution, confirmed caret/scroll return, clean/IME/repeat/Alt guards, editor-only scope, en/zh/en and no focus revival or write replay after unknown receipt verified')
} catch (error) {failure = error}
finally {
  clearTimeout(timer)
  await cleanupFixture({error: failure, browser, lines, backend, releases})
}
