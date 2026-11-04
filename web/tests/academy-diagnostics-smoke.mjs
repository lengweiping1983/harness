import assert from 'node:assert/strict'
import {createServer} from 'node:http'
import {academyDiagnostics} from './academy-diagnostics.mjs'
import {launchBrowser} from './browser-scenario.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'

let requests = 0, browser
const body = JSON.stringify({status: 'ready', result: {run: {id: 'fixture', result: {
  status: 'failed', exit_code: 71, stdout: '', stderr: 'sandbox-exec: sandbox_apply: Operation not permitted',
}}}})
const server = createServer((request, response) => {
  if (request.method === 'POST') {requests++; response.end(body)}
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
  assert.equal(requests, 2)
  assert.equal(original.browserInfrastructure, undefined)
  const records = JSON.parse(messages.find(row => row[0] === 'Academy execution receipts:')[1])
  assert.equal(records.length, 2)
  assert.ok(records.every(record => record.body === body && record.state === 'received'))
  assert.equal(academyDiagnostics(browser), undefined)
} finally {
  console.error = report
  await browser?.close()
  server.closeAllConnections()
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
}
console.log('Academy failure diagnostics: original receipt, both context entry points, unchanged assertion, no replay, route drain and listener cleanup verified')
