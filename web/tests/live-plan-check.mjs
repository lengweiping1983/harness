// 显式在线计划验收，不进入默认 smoke 发现；所有副作用限定于临时项目。
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {once} from 'node:events'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile, mkdir} from 'node:fs/promises'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

if (!process.env.HARNESS_LIVE_API_KEY) throw Error('HARNESS_LIVE_API_KEY is required')
const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.live_model_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
backend.stderr.on('data', () => {}) // 供应商诊断可能包含敏感请求，不作为验收证据输出。
let browser, timer, page
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Live fixture startup timed out')), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Live fixture exited: ' + code)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch { /* 启动日志不是就绪回执。 */ }})
  })
  clearTimeout(timer)
  const base = 'http://127.0.0.1:' + ready.port
  browser = await launchBrowser()
  page = await browser.newPage({viewport: {width: 1440, height: 900}})
  const errors = []
  let sessionId
  page.on('pageerror', error => errors.push(error.message))
  page.on('websocket', socket => {sessionId = new URL(socket.url()).searchParams.get('session')})
  await page.goto(base)
  const input = page.getByRole('textbox', {name: '输入消息', exact: true})
  await input.fill('/plan')
  await input.press('Enter')
  const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/zh-CN.json', import.meta.url)))
  const remove = page.getByRole('button', {name: translations['ui.composerMode.removePlan'], exact: true})
  await remove.waitFor()
  await input.fill('这是隔离项目的真实计划模式验收。先使用 ask_user_question 提问“请选择验收内容”，给出选项“方案A”和“方案B”，等待回答。之后使用 exit_plan_mode 工具提交以 # 开头的 Markdown 计划，计划仅为：批准后用文件工具创建 approved.txt，内容为 live plan A，再读取验证。等待验收者批准。批准前不能创建任何文件，不能通过聊天文字代替工具审批。批准后执行该计划，最后仅回复“计划在线验收完成”。不要执行其他操作。')
  await input.press('Enter')
  const question = page.locator('section[id^="q-"]').filter({hasText: '请选择验收内容'})
  await question.getByRole('button', {name: '方案A', exact: true}).waitFor({timeout: 180000})
  await question.getByRole('button', {name: '方案A', exact: true}).click()
  await question.getByRole('button', {name: translations['ui.components.chat.blocks.QuestionCard.0c1762dddc4f'], exact: true}).click()
  const decision = page.getByRole('button', {name: translations['ui.components.chat.blocks.QuestionCard.b168fdafe0f4'], exact: true})
  const approve = decision.and(page.locator(':enabled'))
  await approve.waitFor({timeout: 180000})
  assert.ok(await approve.evaluate(element => {
    const button = element.getBoundingClientRect()
    const panel = element.closest('[role="region"]')?.getBoundingClientRect()
    return panel && button.top >= panel.top && button.bottom <= panel.bottom
  }), 'Real plan approval must be visible without scrolling the pending panel')
  await assert.rejects(readFile(ready.workspace + '/approved.txt'), {code: 'ENOENT'})
  assert.ok(sessionId)
  const review = page.locator('section[id^="q-"]').filter({has: decision})
  const comments = review.locator('summary').filter({hasText: translations['ui.components.chat.blocks.QuestionCard.6a529601daca']})
  await comments.focus()
  await page.keyboard.press('Enter')
  const feedback = review.getByRole('textbox', {name: translations['ui.components.chat.blocks.QuestionCard.6a612d9e2134'], exact: true})
  await feedback.fill('暂存修改意见，尚未提交')
  assert.equal(await decision.isDisabled(), true)
  await input.fill('尚未发送的草稿应保留')
  await page.reload()
  await decision.waitFor()
  await feedback.waitFor()
  assert.equal(await feedback.inputValue(), '暂存修改意见，尚未提交')
  assert.equal(await decision.isDisabled(), true)
  await feedback.fill('')
  await approve.waitFor()
  await comments.focus()
  await page.keyboard.press('Enter')
  assert.equal(await input.inputValue(), '尚未发送的草稿应保留')
  await remove.waitFor()
  if (process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
    await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/live-plan-review.png', animations: 'disabled'})
  }
  await approve.focus()
  await page.keyboard.press('Enter')
  await page.locator('[data-turn-completion]').waitFor({timeout: 180000})
  assert.equal((await readFile(ready.workspace + '/approved.txt', 'utf8')).trim(), 'live plan A')
  await remove.waitFor({state: 'detached'})
  assert.equal(await input.inputValue(), '尚未发送的草稿应保留')
  await page.reload()
  await page.locator('[data-turn-completion]').waitFor()
  assert.equal(await remove.count(), 0)
  assert.equal(await input.inputValue(), '尚未发送的草稿应保留')
  assert.deepEqual(errors, [])
  if (process.env.HARNESS_TEST_SCREENSHOTS) await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/live-plan-approved.png', animations: 'disabled'})
  console.log('Live plan: clarification, approval after reload, no premature write, keyboard approval, verified artifact and preserved draft passed')
} catch (error) {
  if (page && !page.isClosed()) {
    console.error((await page.locator('body').innerText()).replace(/oc_sk_[\w-]+/g, '<redacted>').slice(-10000))
    if (process.env.HARNESS_TEST_SCREENSHOTS) {
      await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
      await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/live-plan-failure.png', animations: 'disabled'})
    }
  }
  throw error
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
