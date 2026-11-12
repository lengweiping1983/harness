import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {once} from 'node:events'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile, mkdir} from 'node:fs/promises'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, diagnostics = ''
backend.stderr.on('data', value => {diagnostics += value})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Screenshot fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Screenshot fixture exited: ' + code)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch { /* 启动日志不是就绪回执。 */ }})
  })
  clearTimeout(timer)
  browser = await launchBrowser()
  const base = 'http://127.0.0.1:' + ready.port
  for (const language of ['zh-CN', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    const context = await browser.newContext({viewport: {width, height: 900}, permissions: ['clipboard-read', 'clipboard-write']})
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => {errors.push(error.message); console.error('Screenshot page error:', error.message)})
    const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url)))
    const text = key => translations[key]
    assert.ok((await page.request.patch(base + '/api/v1/ui-settings', {data: {language}})).ok())
    await page.addInitScript(theme => {
      localStorage.setItem('harness.ui-theme', theme)
      const state = window.screenshotTest = {captures: 0, finishes: 0, copies: 0, denyCopy: false, holdCopy: false}
      const subscribe = () => () => {}
      window.harnessDesktop = {
        onServiceState: subscribe, onBrowserState: subscribe, onFocusFind: subscribe, onFocusAddress: subscribe, onBrowserError: subscribe,
        browser: async (_action, args) => ({status: 'running', session_id: args?.session_id, tabs: [], active_tab: null}),
        screenshot: async action => {
        if (action === 'finish') {state.finishes++; return null}
        state.captures++
        const canvas = document.createElement('canvas'); canvas.width = 1000; canvas.height = 700
        const ctx = canvas.getContext('2d'); ctx.fillStyle = 'white'; ctx.fillRect(0, 0, 1000, 700)
        ctx.fillStyle = '#1685ff'; ctx.fillRect(0, 0, 1000, 30)
        return canvas.toDataURL('image/png').split(',')[1]
      }}
      const write = navigator.clipboard.write.bind(navigator.clipboard)
      navigator.clipboard.write = items => {
        state.copies++
        if (state.denyCopy) return Promise.reject(new DOMException('Fixture clipboard denied', 'NotAllowedError'))
        const result = write(items)
        return state.holdCopy ? Promise.all([result, new Promise(resolve => {state.releaseCopy = resolve})]) : result
      }
    }, theme)
    await page.goto(base)
    const composer = page.getByRole('textbox', {name: text('ui.components.chat.Composer.a410452649b3'), exact: true})
    await composer.fill('unsent screenshot draft')
    const dialog = page.getByRole('dialog', {name: text('ui.components.chat.ScreenshotCrop.bf8275a62399'), exact: true})
    const attachments = page.getByRole('list', {name: text('ui.components.chat.AttachmentDraft.d630b6745fc5'), exact: true})
    const done = dialog.getByRole('button', {name: text('ui.components.chat.ScreenshotCrop.d137607ad504'), exact: true})
    async function start() {
      await page.getByRole('button', {name: text('ui.components.chat.ScreenshotButton.c95dc99afe57'), exact: true}).click()
      await dialog.locator('.screenshot-stage[data-ready=true]').waitFor()
      const stage = await dialog.locator('.screenshot-stage').boundingBox()
      await page.mouse.move(stage.x + stage.width * .05, stage.y + stage.height * .05)
      await page.mouse.down()
      await page.mouse.move(stage.x + stage.width * .95, stage.y + stage.height * .95)
      await page.mouse.up()
      return stage
    }
    const stage = await start()
    assert.equal(await dialog.locator('.screenshot-text').count(), 0, 'Empty capture has no text editor')
    await dialog.getByRole('button', {name: text('ui.screenshot.text'), exact: true}).click()
    await page.mouse.click(stage.x + stage.width * .12, stage.y + stage.height * .14)
    const editor = dialog.getByRole('textbox', {name: text('ui.screenshot.text'), exact: true})
    await editor.fill('第一行 ABC')
    const firstHeight = (await editor.boundingBox()).height
    await editor.press('Enter')
    await editor.press('Enter')
    await editor.press('ArrowUp')
    await editor.press('End')
    await page.keyboard.insertText('第二行 XYZ')
    assert.equal(await editor.inputValue(), '第一行 ABC\n第二行 XYZ\n')
    assert.ok((await editor.boundingBox()).height > firstHeight * 2, 'Newlines grow the text editor')
    assert.equal(await page.evaluate(() => window.screenshotTest.copies), 0, 'Enter while editing never finishes capture')
    const ime = await editor.evaluate(element => {
      const event = new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true, isComposing: true})
      element.dispatchEvent(event); return {prevented: event.defaultPrevented, focused: document.activeElement === element}
    })
    assert.deepEqual(ime, {prevented: false, focused: true})
    await editor.press('Backspace')
    assert.equal(await editor.inputValue(), '第一行 ABC\n第二行 XY\n', 'Backspace stays inside the text editor')
    await editor.fill('第一行 ABC\n第二行 XYZ\n第三行 END')
    await editor.press('Tab')
    const mark = dialog.locator('.screenshot-text-mark')
    await mark.waitFor()
    assert.equal(await mark.textContent(), '第一行 ABC\n第二行 XYZ\n第三行 END')
    assert.ok(await page.evaluate(() => Boolean(document.activeElement.closest('.screenshot-editor'))), 'Tab remains in capture dialog')
    await mark.dblclick()
    assert.equal(await editor.inputValue(), '第一行 ABC\n第二行 XYZ\n第三行 END', 'Double-click restores all lines')
    await editor.press('Escape')
    assert.equal(await dialog.count(), 1, 'Escape cancels text editing before capture')
    await mark.focus()
    await mark.press('Enter')
    assert.equal(await editor.inputValue(), '第一行 ABC\n第二行 XYZ\n第三行 END')
    await editor.press('Tab')
    // 三行文字整体拖至选区底部，不能按单行高度裁掉后两行。
    const markBox = await mark.boundingBox()
    await page.mouse.move(markBox.x + markBox.width / 2, markBox.y + markBox.height / 2)
    await page.mouse.down()
    await page.mouse.move(stage.x + stage.width * .8, stage.y + stage.height * .98, {steps: 4})
    await page.mouse.up()
    const moved = await mark.boundingBox(), selection = await dialog.locator('.screenshot-selection').boundingBox()
    assert.ok(moved.y + moved.height <= selection.y + selection.height + 2, 'All text lines remain within crop after dragging: ' + JSON.stringify({moved, selection, style: await mark.getAttribute('style')}))
    await mark.dblclick()
    await editor.fill('第一行 ABC\n第二行 XYZ\n第三行 END')
    if (process.env.HARNESS_TEST_SCREENSHOTS) {
      await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
      await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/screenshot-' + [language, theme, width].join('-') + '.png'})
    }
    await page.evaluate(() => {window.screenshotTest.holdCopy = true})
    await done.click()
    await page.waitForFunction(() => window.screenshotTest.releaseCopy)
    assert.equal(await done.isDisabled(), true, 'Completion is disabled while copying')
    assert.equal(await attachments.count(), 0, 'Attachment delivery waits for the single copy attempt')
    await page.evaluate(() => window.screenshotTest.releaseCopy())
    await dialog.waitFor({state: 'hidden'})
    await attachments.getByRole('listitem').waitFor()
    const pixels = await attachments.locator('img').evaluate(async image => {
      const [item] = await navigator.clipboard.read(), copied = await item.getType('image/png')
      const source = await (await fetch(image.src)).blob()
      async function decode(blob) {
        const bitmap = await createImageBitmap(blob), canvas = document.createElement('canvas')
        canvas.width = bitmap.width; canvas.height = bitmap.height
        const ctx = canvas.getContext('2d'); ctx.drawImage(bitmap, 0, 0); bitmap.close()
        return {width: canvas.width, height: canvas.height, data: ctx.getImageData(0, 0, canvas.width, canvas.height).data}
      }
      const left = await decode(copied), right = await decode(source)
      const rows = new Set()
      for (let i = 0; i < left.data.length; i += 4) if (left.data[i] > 200 && left.data[i + 1] < 130 && left.data[i + 2] < 130) rows.add(Math.floor(i / 4 / left.width))
      let groups = 0, previous = -2
      for (const row of rows) {if (row > previous + 1) groups++; previous = row}
      return {width: left.width, height: left.height, identical: left.width === right.width && left.height === right.height && left.data.every((value, i) => value === right.data[i]), textLines: groups}
    })
    assert.ok(pixels.width < 1000 && pixels.height < 700, 'Clipboard contains the crop')
    assert.equal(pixels.identical, true, 'Clipboard pixels match the attached final PNG')
    assert.equal(pixels.textLines, 3, 'Export includes all three annotated lines')
    assert.equal(await composer.inputValue(), 'unsent screenshot draft')
    assert.equal(await composer.evaluate(element => document.activeElement === element), true)
    assert.deepEqual(await page.evaluate(() => [window.screenshotTest.copies, window.screenshotTest.finishes]), [1, 1])
    // 拒绝剪贴板也只投递一次附件，并明确提示失败；取消与保存不额外复制。
    await page.evaluate(() => {window.screenshotTest.holdCopy = false; window.screenshotTest.denyCopy = true})
    await start()
    await done.click()
    await dialog.waitFor({state: 'hidden'})
    assert.equal(await attachments.getByRole('listitem').count(), 2)
    await page.getByText(text('ui.imageActions.clipboardDeniedDesktop'), {exact: true}).waitFor()
    assert.equal(await page.evaluate(() => window.screenshotTest.copies), 2)
    await start()
    const download = page.waitForEvent('download')
    await dialog.getByRole('button', {name: text('ui.screenshot.download'), exact: true}).click()
    assert.match((await download).suggestedFilename(), /\.png$/)
    assert.equal(await page.evaluate(() => window.screenshotTest.copies), 2)
    await dialog.getByRole('button', {name: text('ui.components.chat.ScreenshotCrop.2cd0f3be8738'), exact: true}).click()
    assert.equal(await attachments.getByRole('listitem').count(), 2)
    assert.equal(await composer.inputValue(), 'unsent screenshot draft')
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    assert.deepEqual(errors, [])
    await context.close()
  }
  console.log('Screenshot: eight locale/theme/width combinations; multiline Enter, IME, text edit/drag, final clipboard pixels, copy failure, loading, download, cancel and draft retention passed')
} finally {
  clearTimeout(timer)
  try {await browser?.close()} finally {
    lines.close()
    if (backend.exitCode === null && backend.signalCode === null) {
      const exited = once(backend, 'exit')
      backend.kill('SIGTERM')
      const deadline = setTimeout(() => backend.kill('SIGKILL'), 20000)
      await exited.finally(() => clearTimeout(deadline))
    }
  }
}
