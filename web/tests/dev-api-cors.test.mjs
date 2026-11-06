import assert from 'node:assert/strict'
import {createServer as createHttpServer} from 'node:http'
import {mkdtemp, rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {fileURLToPath} from 'node:url'
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
    server: {...config.config.server, host: '127.0.0.1', port: 0, hmr: false, watch: null},
    optimizeDeps: {noDiscovery: true, include: []},
  })
  await vite.listen()
  const origin = 'http://127.0.0.1:' + vite.httpServer.address().port
  for (const method of ['OPTIONS', 'GET']) {
    const response = await fetch(origin + '/api/v1/status', {method, headers: {Origin: 'http://localhost:5173',
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
  assert.equal(requests.length, 4)
  assert.ok(requests.every(request => request.host === new URL(origin).host))
})
