// 显式在线持续目标验收，不进入默认发现；副作用仅在临时项目。
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
const backend = spawn(fixturePython, ['-m', 'tests.live_model_fixture'], {cwd: root, env: {...process.env, HARNESS_LIVE_GOAL: '1'}, stdio: ['ignore', 'pipe', 'pipe']})
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
  const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/zh-CN.json', import.meta.url)))
  await input.fill('/goal')
  await input.press('Enter')
  const form = page.getByRole('form', {name: translations['ui.components.chat.GoalDock.1d6e17611a7e'], exact: true})
  await form.waitFor()
  const objective = '隔离持续目标在线验收：先使用 ask_user_question 提问“确认目标验收”，选项“继续验收”和“暂不执行”，等待选择。选择继续验收后，用文件工具创建 goal-verified.txt，内容严格为 live goal verified，再使用 read 工具读取核对。最后使用 get_goal 获取精确目标版本和 evidenceCandidates，使用 update_goal action=complete 提交覆盖本目标的 checks，引用实际 read 结果 sourceSeq 作为 evidence_seqs，不得用聊天回复代替目标完成。仅执行这些操作。'
  await form.getByRole('textbox').fill(objective)
  const limits = form.getByRole('spinbutton')
  // 限制持续轮数，不用无界目标执行供应商验收。
  await form.locator('details > summary').click()
  await limits.first().fill('2')
  await form.getByRole('button', {name: translations['ui.components.chat.GoalDock.781565e70304'], exact: true}).click()
  const question = page.locator('section[id^="q-"]').filter({hasText: '确认目标验收'})
  await question.getByRole('button', {name: '继续验收', exact: true}).waitFor({timeout: 180000})
  assert.ok(sessionId)
  const endpoint = base + '/api/v1/sessions/' + sessionId + '/goal'
  const before = (await (await page.request.get(endpoint)).json()).goal
  assert.equal(before.phase, 'active')
  assert.equal(before.maxGoalRounds, 2)
  assert.equal(before.objective, objective)
  await assert.rejects(readFile(ready.workspace + '/goal-verified.txt'), {code: 'ENOENT'})
  await input.fill('未发送的目标验收草稿')
  await page.reload()
  await question.getByRole('button', {name: '继续验收', exact: true}).waitFor()
  assert.equal(await input.inputValue(), '未发送的目标验收草稿')
  await question.getByRole('button', {name: '继续验收', exact: true}).click()
  await question.getByRole('button', {name: translations['ui.components.chat.blocks.QuestionCard.0c1762dddc4f'], exact: true}).click()
  await page.locator('[data-turn-completion]').last().waitFor({timeout: 180000})
  const completed = (await (await page.request.get(endpoint)).json()).goal
  assert.equal(completed.id, before.id)
  assert.equal(completed.objective, objective)
  assert.equal(completed.phase, 'complete')
  const readEvidence = completed.completion.flatMap(check => check.evidence).find(evidence =>
    evidence.name === 'read' && evidence.input.includes('goal-verified.txt') && evidence.preview.includes('live goal verified'))
  assert.ok(readEvidence, 'Completion must cite the actual file read with its observed content')
  const eventResponse = await page.request.get(base + '/api/v1/sessions/' + sessionId + '/events?after=' + (readEvidence.sourceSeq - 1) + '&limit=1')
  assert.ok(eventResponse.ok())
  const event = (await eventResponse.json()).events[0]
  assert.equal(event.seq, readEvidence.sourceSeq)
  assert.equal(event.type, 'tool/result')
  assert.equal(event.data.call_id, readEvidence.callId)
  assert.equal(event.data.name, 'read')
  assert.equal((await readFile(ready.workspace + '/goal-verified.txt', 'utf8')).trim(), 'live goal verified')
  const remove = page.getByRole('button', {name: translations['ui.composerMode.removeGoal'], exact: true})
  await remove.waitFor({state: 'detached'})
  assert.equal(await input.inputValue(), '未发送的目标验收草稿')
  await page.reload()
  await page.locator('[data-turn-completion]').last().waitFor()
  assert.equal((await (await page.request.get(endpoint)).json()).goal.phase, 'complete')
  assert.equal(await remove.count(), 0)
  assert.equal(await input.inputValue(), '未发送的目标验收草稿')
  assert.deepEqual(errors, [])
  console.log('Live goal: clarification, active identity, reload, verified read evidence, completion and draft preservation passed')
} catch (error) {
  if (page && !page.isClosed()) {
    console.error((await page.locator('body').innerText()).replace(/oc_sk_[\w-]+/g, '<redacted>').slice(-10000))
    if (process.env.HARNESS_TEST_SCREENSHOTS) {
      await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
      await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/live-goal-failure.png', animations: 'disabled'})
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
