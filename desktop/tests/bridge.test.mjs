import assert from 'node:assert/strict'
import http from 'node:http'
import {Readable} from 'node:stream'
import {test} from 'node:test'
import {readBridgeRequest, startBridge} from '../bridge.mjs'
import {NativeBrowserError} from '../browser-errors.mjs'
import {browserFailureCases} from './browser-failures.mjs'

function chunks(bytes, width) {
  return Array.from({length:Math.ceil(bytes.length / width)}, (_, index) => bytes.subarray(index * width, (index + 1) * width))
}

test('bridge JSON preserves UTF-8 across every byte boundary and enforces byte limits', async () => {
  const value = {text:'Original 中文😀', path:'/project/原文.txt', nested:{quote:'"\\\r\n'}}
  const bytes = Buffer.from(JSON.stringify(value))
  for (let width = 1; width <= bytes.length; width++) {
    assert.deepEqual(await readBridgeRequest(Readable.from(chunks(bytes, width))), value)
  }
  await assert.rejects(readBridgeRequest(Readable.from([Buffer.from('{"text":"'), Buffer.from([0xff]), Buffer.from('"}')])) , /encoded data/)
  await assert.rejects(readBridgeRequest(Readable.from([Buffer.from('{"text":"'), Buffer.from([0xe4])])) , /encoded data/)
  const exact = Buffer.from(JSON.stringify({text:'x'.repeat(999989)}))
  assert.equal(exact.length, 1_000_000)
  assert.equal((await readBridgeRequest(Readable.from([exact]))).text.length, 999989)
  await assert.rejects(readBridgeRequest(Readable.from([exact, Buffer.from(' ')])), /Request too large/)
  const multibyte = Buffer.from(JSON.stringify({text:'中'.repeat(333334)}))
  await assert.rejects(readBridgeRequest(Readable.from(chunks(multibyte, 4096))), /Request too large/)
})

test('authenticated HTTP bridge delivers exact Unicode and rejects invalid bytes before dispatch', async () => {
  const received = []
  const bridge = await startBridge(async args => {received.push(args); if (args.failure) throw new NativeBrowserError(args.failure, args.params); return args})
  const post = (body, token = bridge.token) => new Promise((resolve, reject) => {
    const request = http.request(bridge.url, {method:'POST', headers:{Authorization:'Bearer ' + token}}, response => {
      const data = []
      response.on('data', chunk => data.push(chunk))
      response.on('end', () => resolve({status:response.statusCode, body:JSON.parse(Buffer.concat(data).toString('utf8'))}))
      response.on('error', reject)
    })
    request.on('error', reject)
    for (const chunk of chunks(body, 1)) request.write(chunk)
    request.end()
  })
  try {
    const value = {session_id:'fixture', text:'中文😀', path:'/project/原文.txt'}
    const response = await post(Buffer.from(JSON.stringify(value)))
    assert.equal(response.status, 200)
    assert.deepEqual(response.body, value)
    assert.equal((await post(Buffer.from('{"text":"\xff"}', 'latin1'))).status, 400)
    assert.equal((await post(Buffer.from('{}'), 'invalid')).status, 401)
    assert.deepEqual(received, [value])
    for (const [kind,params] of browserFailureCases) {
      const response = await post(Buffer.from(JSON.stringify({failure:kind,params})))
      const expected = new NativeBrowserError(kind,params)
      assert.equal(response.status, 400)
      assert.deepEqual(response.body, {error:expected.message, code:expected.code, copy:expected.copy})
    }
  } finally {await bridge.close()}
})
