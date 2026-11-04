import assert from 'node:assert/strict'
import {EventEmitter, once} from 'node:events'
import {spawn} from 'node:child_process'
import {test} from 'node:test'
import {cleanupFixture, diagnoseFailure} from './fixture-cleanup.mjs'
import {observeAcademyExecutions, academyDiagnostics} from './academy-diagnostics.mjs'

test('a pending execution body cannot block cleanup, replace the assertion or revive diagnostics later', async () => {
  const browser = new EventEmitter(), context = new EventEmitter(), backend = child()
  const original = Error('original output assertion'), messages = [], report = console.error
  let release, closed = false
  Object.assign(browser, {contexts: () => [context], newContext: async () => context, newPage: async () => ({}),
    close: async () => {closed = true; browser.emit('disconnected')}})
  observeAcademyExecutions(browser)
  const collector = academyDiagnostics(browser)
  context.emit('response', {request: () => ({method: () => 'POST'}),
    url: () => 'http://127.0.0.1/api/v1/academy/projects/fixture/run', status: () => 200,
    text: () => new Promise(resolve => {release = resolve})})
  try {
    console.error = (...args) => messages.push(args)
    await assert.rejects(cleanupFixture({error: original, browser, backend, diagnosticMs: 5}), error => error === original)
    assert.ok(closed)
    assert.deepEqual(backend.signals, ['SIGINT'])
    assert.match(messages[0][1].message, /diagnostics timed out/)
    assert.equal(JSON.parse(messages[1][1])[0].state, 'pending')
    release('late raw output')
    await collector.wait()
    assert.equal(collector.snapshot()[0].state, 'pending')
    assert.equal(context.listenerCount('response'), 0)
  } finally {console.error = report; release?.('cleanup')}
})

function child({unresponsive = false} = {}) {
  const process = new EventEmitter()
  process.exitCode = null; process.signalCode = null; process.signals = []
  process.kill = signal => {
    process.signals.push(signal)
    if (!unresponsive) {process.signalCode = signal; process.emit('exit', null, signal)}
    return true
  }
  return process
}

test('original scenario error and browser cleanup failure preserve identity and still stop the backend', async () => {
  const original = Error('original scenario'), browserFailure = Error('browser failed'), backend = child()
  const calls = []
  await assert.rejects(cleanupFixture({error: original, browser: {close: async () => {calls.push('browser'); throw browserFailure}},
    lines: {close: () => calls.push('lines')}, backend, releases: [() => calls.push('release')]}), error => {
    assert.deepEqual(error.errors, [original, browserFailure]); return true
  })
  assert.deepEqual(calls, ['release', 'browser', 'lines'])
  assert.deepEqual(backend.signals, ['SIGINT'])
})

test('hung browser and unresponsive backend have separate deadlines and retain original failure', async () => {
  const original = Error('primary failure'), backend = child({unresponsive: true})
  let linesClosed = false
  await assert.rejects(cleanupFixture({error: original, browser: {close: () => new Promise(() => {})},
    lines: {close: () => {linesClosed = true}}, backend, closeMs: 5, graceMs: 5, killMs: 5}), error => {
    assert.equal(error.errors[0], original)
    assert.match(error.errors[1].message, /Browser cleanup timed out/)
    assert.match(error.errors[2].message, /did not exit after SIGKILL/)
    return true
  })
  assert.ok(linesClosed)
  assert.deepEqual(backend.signals, ['SIGINT', 'SIGKILL'])
  assert.equal(backend.listenerCount('exit'), 0)
})

test('diagnostic exceptions and deadlines cannot replace the original assertion', async () => {
  const original = Error('original assertion'), reports = []
  let finalDiagnostic = false
  await assert.rejects(diagnoseFailure(original, [async () => {throw Error('screenshot failed')},
    () => new Promise(() => {}), async () => {finalDiagnostic = true}],
    {timeoutMs: 5, report: (...args) => reports.push(args)}), error => error === original)
  assert.equal(reports.length, 2)
  assert.ok(finalDiagnostic)
})

test('real fixture child exits even when browser cleanup fails', async () => {
  const backend = spawn(process.execPath, ['-e', "console.log('ready');setInterval(()=>{},1000)"], {stdio: ['ignore', 'pipe', 'pipe']})
  try {
    await once(backend.stdout, 'data')
    const failure = Error('browser close failure')
    await assert.rejects(cleanupFixture({browser: {close: async () => {throw failure}}, backend,
      graceMs: 1000, killMs: 1000}), error => error === failure)
    assert.ok(backend.exitCode !== null || backend.signalCode !== null)
  } finally {
    if (backend.exitCode === null && backend.signalCode === null) {const exit = once(backend, 'exit'); backend.kill('SIGKILL'); await exit}
  }
})
