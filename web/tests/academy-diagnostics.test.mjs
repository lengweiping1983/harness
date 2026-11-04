import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {test} from 'node:test'
import {academyDiagnostics, observeAcademyExecutions} from './academy-diagnostics.mjs'

function fixture() {
  const context = new EventEmitter(), browser = new EventEmitter()
  browser.contexts = () => [context]
  browser.newContext = async () => context
  browser.newPage = async () => ({context: () => context})
  observeAcademyExecutions(browser)
  const respond = (body, pathname = '/api/v1/academy/projects/fixture/run', method = 'POST') => {
    context.emit('response', {request: () => ({method: () => method}), url: () => 'http://127.0.0.1' + pathname,
      status: () => 200, text: async () => typeof body === 'function' ? body() : body})
  }
  return {context, browser, respond, collector: academyDiagnostics(browser)}
}

test('execution diagnostics preserve raw failures, reject unrelated traffic and bound retained bodies', async () => {
  const {browser, context, respond, collector} = fixture()
  respond('secret', '/api/v1/settings'); respond('not execution', undefined, 'GET')
  for (let i = 0; i < 10; i++) respond(String(i))
  respond('x'.repeat(65537), '/api/v1/academy/projects/fixture/check')
  respond(() => {throw Error('response connection closed')})
  await collector.wait()
  const records = collector.snapshot()
  assert.equal(records.length, 8)
  assert.equal(records[0].body, '4')
  assert.equal(records[6].body.length, 65536)
  assert.equal(records[6].truncated, true)
  assert.deepEqual(records[7], {url: 'http://127.0.0.1/api/v1/academy/projects/fixture/run',
    status: 200, state: 'unavailable', error: 'response connection closed'})
  browser.emit('disconnected')
  assert.equal(context.listenerCount('response'), 0)
  assert.equal(academyDiagnostics(browser), undefined)
})

test('closing diagnostics detaches listeners and prevents a late body from altering the snapshot', async () => {
  const {context, respond, collector} = fixture()
  let release
  respond(() => new Promise(resolve => {release = resolve}))
  const waiting = collector.wait()
  const before = collector.snapshot()
  collector.close()
  release('late response')
  await waiting
  assert.deepEqual(collector.snapshot(), before)
  assert.equal(before[0].state, 'pending')
  assert.equal(context.listenerCount('response'), 0)
})

