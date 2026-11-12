import assert from 'node:assert/strict'
import {createServer as createHttpServer, request as httpRequest} from 'node:http'
import {mkdtemp, rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {fileURLToPath} from 'node:url'
import {connect} from 'node:net'
import test from 'node:test'
import {createServer, loadConfigFromFile} from 'vite'

test('Vite delegates API preflights and response CORS to the backend without adding permissions', {timeout: 20000}, async t => {
  const root = fileURLToPath(new URL('../', import.meta.url))
  const cache = await mkdtemp(join(tmpdir(), 'harness-dev-cors-'))
  const requests = []
  const backend = createHttpServer((request, response) => {
    requests.push({method: request.method, host: request.headers.host, origin: request.headers.origin})
    const origin = request.headers.origin
    if (origin && origin !== 'http://' + request.headers.host) {
      response.writeHead(403, {'Content-Type': 'application/json'})
      response.end(JSON.stringify({code: 'request/origin'}))
      return
    }
    if (origin) {
      response.setHeader('Access-Control-Allow-Origin', origin)
      response.setHeader('Access-Control-Allow-Methods', 'GET,POST,PATCH,DELETE,OPTIONS')
    }
    response.writeHead(request.method === 'OPTIONS' ? 204 : 200, {'Content-Type': 'application/json'})
    response.end(request.method === 'OPTIONS' ? undefined : JSON.stringify({ok: true}))
  })
  let vite
  t.after(async () => {
    try {await vite?.close()}
    finally {
      backend.closeAllConnections()
      await new Promise((resolve, reject) => backend.close(error => error ? reject(error) : resolve()))
      await rm(cache, {recursive: true, force: true})
    }
  })
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve))
  const previousBackend = process.env.HARNESS_BACKEND_URL
  let config
  try {
    process.env.HARNESS_BACKEND_URL = 'http://127.0.0.1:' + backend.address().port
    config = await loadConfigFromFile({command: 'serve', mode: 'test'}, join(root, 'vite.config.ts'), root)
  } finally {
    if (previousBackend === undefined) delete process.env.HARNESS_BACKEND_URL
    else process.env.HARNESS_BACKEND_URL = previousBackend
  }
  assert.ok(config)
  vite = await createServer({
    configFile: false, root, cacheDir: cache, logLevel: 'error',
    plugins: config.config.plugins.filter(plugin => plugin?.name === 'harness-api-continue'),
    server: {...config.config.server, host: '127.0.0.1', port: 0, hmr: false, watch: null},
    optimizeDeps: {noDiscovery: true, include: []},
  })
  await vite.listen()
  const origin = 'http://127.0.0.1:' + vite.httpServer.address().port
  for (const [method, path] of [['OPTIONS', '/api/v1/status'], ['GET', '/api/v1/status'], ['GET', '/api'], ['OPTIONS', '/api/foo']]) {
    const response = await fetch(origin + path, {method, headers: {Origin: 'http://localhost:5173',
      ...(method === 'OPTIONS' ? {'Access-Control-Request-Method': 'POST'} : {})}})
    assert.equal(response.status, 403)
    assert.equal(response.headers.get('access-control-allow-origin'), null)
    assert.equal(response.headers.get('access-control-allow-methods'), null)
    assert.deepEqual(await response.json(), {code: 'request/origin'})
  }
  const allowed = await fetch(origin + '/api/v1/status', {method: 'OPTIONS', headers: {Origin: origin, 'Access-Control-Request-Method': 'POST'}})
  assert.equal(allowed.status, 204)
  assert.equal(allowed.headers.get('access-control-allow-origin'), origin)
  assert.equal(allowed.headers.get('access-control-allow-methods'), 'GET,POST,PATCH,DELETE,OPTIONS')
  await allowed.arrayBuffer()
  const native = await fetch(origin + '/api/v1/status')
  assert.equal(native.status, 200)
  assert.deepEqual(await native.json(), {ok: true})
  assert.equal(requests.length, 6)
  assert.ok(requests.every(request => request.host === new URL(origin).host))
})

test('Vite forwards the API namespace and sends headers before awaiting oversized bodies', {timeout: 20000}, async t => {
  const root = fileURLToPath(new URL('../', import.meta.url))
  const cache = await mkdtemp(join(tmpdir(), 'harness-dev-http-'))
  const requests = []
  let rejectedBytes = 0
  const backend = createHttpServer((request, response) => {
    requests.push(request.url)
    const oversized = Number(request.headers['content-length'] ?? 0) > 4194304
    if (oversized) request.on('data', chunk => {rejectedBytes += chunk.length})
    if (request.url === '/api/v1/echo' && !oversized) {
      const chunks = []
      request.on('data', chunk => chunks.push(chunk))
      request.on('end', () => response.end(Buffer.concat(chunks)))
      return
    }
    response.writeHead(oversized ? 413 : 404, {
      'Content-Type': 'application/json', 'Cache-Control': 'no-store',
      'X-Request-ID': 'fixture-request', 'Connection': 'close',
    })
    response.end(JSON.stringify({code: oversized ? 'request/body-too-large' : 'route/not-found'}))
  })
  backend.on('checkContinue', (request, response) => {
    if (Number(request.headers['content-length'] ?? 0) <= 4194304) response.writeContinue()
    backend.emit('request', request, response)
  })
  let vite
  t.after(async () => {
    try {await vite?.close()}
    finally {
      backend.closeAllConnections()
      await new Promise((resolve, reject) => backend.close(error => error ? reject(error) : resolve()))
      await rm(cache, {recursive: true, force: true})
    }
  })
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve))
  const previousBackend = process.env.HARNESS_BACKEND_URL
  let config
  try {
    process.env.HARNESS_BACKEND_URL = 'http://127.0.0.1:' + backend.address().port
    config = await loadConfigFromFile({command: 'serve', mode: 'test'}, join(root, 'vite.config.ts'), root)
  } finally {
    if (previousBackend === undefined) delete process.env.HARNESS_BACKEND_URL
    else process.env.HARNESS_BACKEND_URL = previousBackend
  }
  vite = await createServer({
    configFile: false, root, cacheDir: cache, logLevel: 'error',
    plugins: config.config.plugins.filter(plugin => plugin?.name === 'harness-api-continue'),
    server: {...config.config.server, host: '127.0.0.1', port: 0, hmr: false, watch: null},
    optimizeDeps: {noDiscovery: true, include: []},
  })
  await vite.listen()
  const port = vite.httpServer.address().port
  const base = 'http://127.0.0.1:' + port
  for (const path of ['/api', '/api/', '/api/foo', '/api?query=1', '/api/v1/missing']) {
    for (const accept of ['text/html', 'application/json']) {
      const response = await fetch(base + path, {headers: {Accept: accept}})
      assert.equal(response.status, 404, path + ' must reach the backend')
      assert.equal(response.headers.get('x-request-id'), 'fixture-request')
      assert.deepEqual(await response.json(), {code: 'route/not-found'})
    }
  }
  const head = await fetch(base + '/api/missing', {method: 'HEAD'})
  assert.equal(head.status, 404)
  assert.equal(await head.text(), '')
  const count = requests.length
  await fetch(base + '/apiculture', {headers: {Accept: 'application/json'}}).then(response => response.arrayBuffer())
  assert.equal(requests.length, count, 'Similar SPA paths must not be treated as API paths')

  const raw = await new Promise((resolve, reject) => {
    const socket = connect(port, '127.0.0.1')
    const chunks = []
    socket.setTimeout(3000, () => socket.destroy(new Error('No early 413 response')))
    socket.on('error', reject)
    socket.on('data', chunk => chunks.push(chunk))
    socket.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    socket.on('connect', () => socket.write('POST /api/v1/sessions HTTP/1.1\r\nHost: 127.0.0.1:' + port
      + '\r\nContent-Type: application/json\r\nContent-Length: 5242880\r\n\r\n'))
  })
  assert.match(raw, /^HTTP\/1\.1 413 /)
  assert.match(raw, /x-request-id: fixture-request/i)
  assert.match(raw, /request\/body-too-large/)
  const uploaded = await fetch(base + '/api/v1/sessions', {method: 'POST', body: 'x'.repeat(4194304 + 512)})
  assert.equal(uploaded.status, 413, 'A client sending a real body must still receive the early rejection')
  assert.deepEqual(await uploaded.json(), {code: 'request/body-too-large'})
  const original = JSON.stringify({text: 'Original 原文'})
  for (const [path, body, status] of [['/api/v1/sessions', 'x'.repeat(4194304 + 512), 413],
                                    ['/api/v1/echo', original, 200]]) await new Promise((resolve, reject) => {
    let continued = 0
    const request = httpRequest(base + path, {method: 'POST', headers: {
      Expect: '100-continue', 'Content-Length': String(Buffer.byteLength(body)),
    }}, response => {
      const chunks = []
      response.on('data', chunk => chunks.push(chunk))
      response.on('error', reject)
      response.on('end', () => {
        try {
          assert.equal(response.statusCode, status)
          assert.equal(continued, status === 200 ? 1 : 0)
          const text = Buffer.concat(chunks).toString('utf8')
          if (status === 413) assert.deepEqual(JSON.parse(text), {code: 'request/body-too-large'})
          else assert.equal(text, original)
          resolve()
        } catch (error) {reject(error)}
      })
    })
    request.on('error', reject)
    request.once('continue', () => {continued++; request.end(body)})
    request.flushHeaders()
  })
  const echo = await fetch(base + '/api/v1/echo', {method: 'POST', body: original})
  assert.equal(echo.status, 200)
  assert.equal(await echo.text(), original, 'Accepted request bodies must be sent once and preserved exactly')
  assert.equal(rejectedBytes, 0, 'Rejected uploads must not be forwarded to the backend')
})
