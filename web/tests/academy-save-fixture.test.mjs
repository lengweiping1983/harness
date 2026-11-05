import assert from 'node:assert/strict'
import {test} from 'node:test'
import {interceptSaveReceipt} from './academy-save-fixture.mjs'

function fixture({status = 200, value, fetchError, abortError, unrouteError} = {}) {
  let handler, routed
  const calls = []
  const response = {status: () => status, json: async () => structuredClone(value)}
  const route = {
    request: () => ({method: () => 'POST'}),
    fetch: async () => {calls.push('fetch'); if (fetchError) throw fetchError; return response},
    fulfill: async options => {calls.push('fulfill'); routed = options.json},
    abort: async () => {calls.push('abort'); if (abortError) throw abortError},
    fallback: async () => {calls.push('fallback')},
  }
  const page = {
    route: async (endpoint, callback) => {assert.equal(endpoint, 'save'); handler = callback},
    unroute: async (endpoint, callback) => {
      assert.equal(endpoint, 'save'); assert.equal(callback, handler)
      calls.push('unroute'); if (unrouteError) throw unrouteError
    },
  }
  const dispatch = async (method = 'POST') => {
    // 模拟框架异步分发；回调本身必须完成，错误只能由场景等待方接收。
    await assert.doesNotReject(() => handler({...route, request: () => ({method: () => method})}))
  }
  return {page, calls, routed: () => routed, submit: dispatch, read: () => dispatch('GET')}
}

test('only a ready save source is changed and the request is fetched once', async () => {
  const value = {status: 'ready', result: {source: {path: 'main.py', content: 'original'}}}
  const scene = fixture({value})
  await interceptSaveReceipt(scene.page, 'save', source => {source.path = 'foreign.py'}, scene.submit)
  assert.deepEqual(scene.routed(), {status: 'ready', result: {source: {path: 'foreign.py', content: 'original'}}})
  assert.equal(value.result.source.path, 'main.py')
  assert.deepEqual(scene.calls, ['fetch', 'fulfill', 'unroute'])
})

test('a GET file listing on the same endpoint does not consume the save interception', async () => {
  const scene = fixture({value: {status: 'ready', result: {source: {path: 'main.py', content: 'saved'}}}})
  await interceptSaveReceipt(scene.page, 'save', source => {source.path = 'foreign.py'}, async () => {
    await scene.read()
    assert.deepEqual(scene.calls, ['fallback'])
    await scene.submit()
  })
  assert.equal(scene.routed().result.source.path, 'foreign.py')
  assert.deepEqual(scene.calls, ['fallback', 'fetch', 'fulfill', 'unroute'])
})

test('HTTP error envelopes, failed receipts and missing sources fail the scenario with raw diagnostics', async () => {
  for (const [status, value] of [
    [400, {error: 'original validation failure', code: 'invalid-request'}],
    [200, {status: 'failed', result: {error_code: 'stale-file'}}],
    [200, {status: 'ready', result: {}}],
  ]) {
    const scene = fixture({status, value})
    await assert.rejects(interceptSaveReceipt(scene.page, 'save', assert.fail, scene.submit), error => {
      assert.equal(error.name, 'AssertionError')
      assert.ok(error.message.includes(`HTTP ${status}`))
      assert.ok(error.message.includes(JSON.stringify(value)))
      return true
    })
    assert.deepEqual(scene.calls, ['fetch', 'abort', 'unroute'])
  }
})

test('interception and cleanup retain each original error without replaying the save', async () => {
  const fetchError = Error('original fetch error'), abortError = Error('original abort error'), unrouteError = Error('original unroute error')
  const scene = fixture({fetchError, abortError, unrouteError})
  await assert.rejects(interceptSaveReceipt(scene.page, 'save', assert.fail, scene.submit), error => {
    assert.deepEqual(error.errors[0].errors, [fetchError, abortError])
    assert.equal(error.errors[1], unrouteError)
    return true
  })
  assert.deepEqual(scene.calls, ['fetch', 'abort', 'unroute'])
})
