import assert from 'node:assert/strict'
import {createServer} from 'node:http'
import {academyDiagnostics} from './academy-diagnostics.mjs'
import {launchBrowser} from './browser-scenario.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'

let requests = 0, browser
const recoveryRequests = []
const body = JSON.stringify({status: 'ready', result: {run: {id: 'fixture', result: {
  status: 'failed', exit_code: 71, stdout: '', stderr: 'sandbox-exec: sandbox_apply: Operation not permitted',
}}}})
const tutorBody = JSON.stringify({tutor_turns: [{id: 'tutor', actions: [{tool: 'run_example',
  run_id: 'fixture', output: JSON.parse(body).result.run.result.stderr}]}]})
const runsBody = JSON.stringify({runs: [JSON.parse(body).result.run]})
const server = createServer((request, response) => {
  if (request.method === 'POST') {requests++; response.end(body)}
  else if (request.url.startsWith('/api/v1/academy/')) {
    recoveryRequests.push(request.url)
    response.setHeader('Content-Type', 'application/json')
    response.end(request.url.endsWith('/tutor-turns') ? tutorBody : request.url.endsWith('/runs') ? runsBody : body)
  }
  else {response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><title>diagnostics fixture</title>')}
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const original = new assert.AssertionError({message: 'expected successful course output'})
const messages = [], report = console.error
try {
  browser = await launchBrowser()
  // 两种页面创建入口都要捕获，分别归属于自己的上下文。
  const context = await browser.newContext()
  for (const page of [await context.newPage(), await browser.newPage()]) {
    await page.goto('http://127.0.0.1:' + server.address().port)
    await page.evaluate(() => fetch('/api/v1/academy/projects/fixture/run', {method: 'POST'}).then(value => value.text()))
  }
  const page = await context.newPage()
  await page.goto('http://127.0.0.1:' + server.address().port)
  // 后端已接受写入但浏览器收不到响应，原文只能从产品实际发起的回查获得。
  await page.route('**/api/v1/academy/projects/recovered/run', async route => {
    await route.fetch()
    await route.abort('failed')
  })
  assert.equal(await page.evaluate(() => fetch('/api/v1/academy/projects/recovered/run', {method: 'POST'})
    .then(() => false, () => true)), true)
  await page.unrouteAll({behavior: 'wait'})
  const recoveryPaths = ['/api/v1/academy/receipts/lost-editor',
    '/api/v1/academy/projects/fixture/tutor-turns', '/api/v1/academy/projects/fixture/runs']
  await page.evaluate(async paths => {
    await fetch('/api/v1/academy/projects/fixture/tutor-turns', {method: 'POST'}).then(response => response.text())
    for (const path of paths) await fetch(path).then(response => response.text())
  }, recoveryPaths)
  let entered, release, routeFinished = false
  const started = new Promise(resolve => {entered = resolve})
  const gate = new Promise(resolve => {release = resolve})
  await page.route('**/drain', async route => {
    entered()
    await gate
    const response = await route.fetch()
    await route.fulfill({response})
    routeFinished = true
  })
  await page.evaluate(() => {void fetch('/drain')})
  await started
  console.error = (...args) => messages.push(args)
  await assert.rejects(cleanupFixture({error: original, browser, releases: [async () => {
    release()
    await page.unrouteAll({behavior: 'wait'})
    assert.equal(routeFinished, true, 'accepted routes must settle before the browser closes')
  }]}), error => error === original)
  assert.equal(requests, 4)
  assert.deepEqual(recoveryRequests, recoveryPaths, 'diagnostics must not initiate extra recovery reads')
  assert.equal(original.browserInfrastructure, undefined)
  const records = JSON.parse(messages.find(row => row[0] === 'Academy execution receipts:')[1])
  assert.equal(records.length, 6)
  assert.ok(records.every(record => record.state === 'received'))
  assert.ok(records.filter(record => record.method === 'POST').every(record => record.body === body))
  assert.deepEqual(records.filter(record => record.method === 'GET').map(record => [new URL(record.url).pathname, record.body]),
    [[recoveryPaths[0], body], [recoveryPaths[1], tutorBody], [recoveryPaths[2], runsBody]])
  assert.equal(academyDiagnostics(browser), undefined)
} finally {
  console.error = report
  await browser?.close()
  server.closeAllConnections()
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
}
console.log('Academy failure diagnostics: original POST and recovery GET receipts, tutor history and runs, both context entry points, unchanged assertion, no extra reads or replay, route drain and listener cleanup verified')
