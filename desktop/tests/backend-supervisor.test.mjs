import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {test} from 'node:test'
import {BackendSupervisor} from '../backend-supervisor.mjs'

function pendingBackend() {
  let resolve
  const startup = new Promise(done => {resolve = done})
  const child = Object.assign(new EventEmitter(), {exitCode:null, signalCode:null, pid:123})
  const signals = []
  child.kill = signal => {
    signals.push(signal)
    queueMicrotask(() => {child.exitCode = 0; child.emit('exit', 0, null)})
    return true
  }
  const backend = {child, port:12345}
  const states = []
  let launches = 0
  const supervisor = new BackendSupervisor({}, state => states.push(state), () => {launches++; return startup})
  return {supervisor, child, backend, states, signals, ready:() => resolve(backend), launches:() => launches}
}

test('concurrent starts share startup and reuse the running backend', async () => {
  const fixture = pendingBackend()
  const first = fixture.supervisor.start()
  assert.equal(fixture.supervisor.start(), first)
  fixture.ready()
  assert.equal(await first, fixture.backend)
  assert.equal(await fixture.supervisor.start(), fixture.backend)
  assert.equal(fixture.launches(), 1)
  assert.deepEqual(fixture.states, ['starting', 'running'])
  await fixture.supervisor.close()
})

test('closing during startup owns the arriving backend and stops it once', async () => {
  const fixture = pendingBackend()
  const start = fixture.supervisor.start()
  const rejected = assert.rejects(start, /Desktop is closing/)
  const close = fixture.supervisor.close()
  assert.equal(fixture.supervisor.close(), close)
  fixture.ready()
  await Promise.all([rejected, close])
  assert.equal(fixture.supervisor.backend, fixture.backend)
  assert.deepEqual(fixture.signals, ['SIGINT'])
  assert.equal(fixture.child.exitCode, 0)
  assert.deepEqual(fixture.states, ['starting'])
  await assert.rejects(fixture.supervisor.start(), /Desktop is closing/)
})

test('closing during startup reports a failure to stop the arriving backend', async () => {
  const fixture = pendingBackend()
  fixture.child.kill = () => {throw Error('stop denied')}
  const start = fixture.supervisor.start()
  const rejected = assert.rejects(start, /Desktop is closing/)
  const close = fixture.supervisor.close()
  const failed = assert.rejects(close, /stop denied/)
  fixture.ready()
  await Promise.all([rejected, failed])
  assert.equal(fixture.supervisor.backend, fixture.backend)
  assert.equal(fixture.child.exitCode, null)
})

test('an exited owned backend publishes stopped', async () => {
  const fixture = pendingBackend()
  const start = fixture.supervisor.start()
  fixture.ready()
  await start
  fixture.child.exitCode = 1
  fixture.child.emit('exit', 1, null)
  assert.equal(fixture.supervisor.status, 'stopped')
  assert.deepEqual(fixture.states, ['starting', 'running', 'stopped'])
  await fixture.supervisor.close()
})
