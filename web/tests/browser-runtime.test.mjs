import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {test} from 'node:test'
import {canShareFixture, createBrowserRuntime, fixtureDispatcher} from './browser-runtime.mjs'

test('configuration, pre hooks and product transforms retain dedicated Vite ownership', () => {
  const plugin = {name: 'virtual', resolveId: () => {}, load: () => {}, configureServer: () => {}}
  assert.ok(canShareFixture({plugins: [plugin]}, '/entry.js'))
  for (const vite of [{server: {proxy: {}}}, {cacheDir: 'own'}, {plugins: [{...plugin, enforce: 'pre'}]},
    {plugins: [{...plugin, transform: () => {}}]}, {plugins: [{...plugin, config: () => {}}]}, {plugins: [Promise.resolve(plugin)]}]) {
    assert.equal(canShareFixture(vite, '/entry.js'), false)
  }
  assert.equal(canShareFixture({}, undefined), false)
})

test('fixture lease drains before invalidation and removes old routes, modules and locale readiness', async () => {
  const dispatcher = fixtureDispatcher(), calls = []
  let middleware, finishDrain
  const server = {middlewares: {use: value => {middleware = value}},
    transformIndexHtml: () => 'real-server', waitForRequestsIdle: () => new Promise(resolve => {finishDrain = resolve}),
    moduleGraph: {invalidateAll: () => calls.push('invalidate')}}
  dispatcher.plugin.configureServer(server)
  for (const text of ['first', 'replacement']) {
    const detach = await dispatcher.attach(server, [{name: text,
      resolveId: id => id === '/same.js' ? id : undefined,
      load: id => id === '/same.js' ? text : undefined,
      configureServer: facade => {facade.middlewares.use('/fixture', (req, res) => {res.value = text + req.url + facade.transformIndexHtml()})},
    }], '/same.js')
    assert.equal(await dispatcher.plugin.load('/same.js'), text)
    assert.match(dispatcher.plugin.transform(text, '/same.js'), /__harnessFixtureReady/)
    const response = {}, request = {url: '/fixture?value=1'}
    middleware(request, response, () => {throw Error('matched fixture should respond')})
    assert.equal(response.value, text + '/?value=1real-server')
    let unmatched = false; middleware({url: '/fixture-other'}, {}, () => {unmatched = true}); assert.ok(unmatched)
    await assert.rejects(dispatcher.attach(server, [], '/other.js'), /already leased/)
    const draining = detach()
    assert.equal(await dispatcher.plugin.load('/same.js'), text, 'late conversion still belongs to its lease')
    finishDrain(); await draining
    assert.equal(await dispatcher.plugin.load('/same.js'), undefined)
    assert.equal(dispatcher.plugin.transform(text, '/same.js'), undefined)
  }
  assert.equal(calls.length, 2)
})

test('setup errors revoke the failed route registration rather than leaking into the next lease', async () => {
  const dispatcher = fixtureDispatcher(), original = Error('middleware setup failed')
  const server = {moduleGraph: {invalidateAll() {}}, waitForRequestsIdle: async () => {}}
  await assert.rejects(dispatcher.attach(server, [{configureServer() {throw original}}], '/entry.js'), error => error === original)
  const detach = await dispatcher.attach(server, [], '/entry.js'); await detach()
})

test('suite closes all browser connections and both owned services even when browser cleanup fails', async () => {
  const calls = [], connections = []
  const runtime = await createBrowserRuntime({
    browserFactory: async options => {
      assert.equal(options.host, '127.0.0.1')
      return {wsEndpoint: () => 'fixture', close: async () => {calls.push('chromium')}}
    },
    connectFactory: async () => {
      const connection = Object.assign(new EventEmitter(), {version: () => 'fixture', close: async () => {connection.emit('disconnected')}})
      connections.push(connection); return connection
    },
    serverFactory: async () => ({listen: async () => {}, transformRequest: async () => ({}), waitForRequestsIdle: async () => {},
      close: async () => {calls.push('vite')}}),
  })
  const a = await runtime.connect(), b = await runtime.connect()
  const original = Error('connection cleanup failed')
  a.close = async () => {calls.push('a'); throw original}
  b.close = async () => {calls.push('b'); b.emit('disconnected')}
  try {
    await assert.rejects(runtime.close(), error => {assert.equal(error.errors[0].errors[0], original); return true})
    assert.deepEqual(calls, ['a', 'b', 'vite', 'chromium'])
    await assert.rejects(runtime.connect(), /closing/)
  } finally {
    const {rm} = await import('node:fs/promises')
    await rm(runtime.directory, {recursive: true, force: true})
  }
})
