import assert from 'node:assert/strict'
import {EventEmitter, once} from 'node:events'
import {spawn} from 'node:child_process'
import {test} from 'node:test'
import {stopChild} from '../stop-child.mjs'

function childProcess() {
  const child = Object.assign(new EventEmitter(), {exitCode: null, signalCode: null, pid: 123})
  child.signals = []
  child.kill = signal => {child.signals.push(signal); return true}
  child.exit = () => {child.exitCode = 0; child.emit('exit', 0, null)}
  return child
}

test('concurrent stops share one owned shutdown and one interrupt', async () => {
  const child = childProcess()
  const first = stopChild(child)
  const second = stopChild(child)
  child.exit()
  await Promise.all([first, second])
  assert.deepEqual(child.signals, ['SIGINT'])
  assert.equal(first, second)
  assert.equal(child.listenerCount('exit'), 0)
})

test('grace expiry escalates once and exit removes the listener', async () => {
  const child = childProcess()
  child.kill = signal => {
    child.signals.push(signal)
    if (signal === 'SIGKILL') queueMicrotask(child.exit)
    return true
  }
  await stopChild(child, {graceMs: 1, killMs: 1000})
  assert.deepEqual(child.signals, ['SIGINT', 'SIGKILL'])
  assert.equal(child.listenerCount('exit'), 0)
})

test('kill failure rejects and allows a later explicit stop attempt', async () => {
  const child = childProcess()
  child.kill = () => {throw Error('permission denied')}
  await assert.rejects(stopChild(child), /permission denied/)
  assert.equal(child.listenerCount('exit'), 0)
  child.kill = signal => {child.signals.push(signal); queueMicrotask(child.exit); return true}
  await stopChild(child)
  assert.deepEqual(child.signals, ['SIGINT'])
})

test('unresponsive child reports failure after the final deadline', async () => {
  const child = childProcess()
  await assert.rejects(stopChild(child, {graceMs: 1, killMs: 1}), /did not exit/)
  assert.equal(child.listenerCount('exit'), 0)
})

test('stopping a real local child waits for its exit', {timeout: 10000}, async t => {
  const child = spawn(process.execPath, ['-e', 'process.on("SIGINT",()=>process.exit(0));setInterval(()=>{},1000);process.stdout.write("ready")'], {stdio: ['ignore', 'pipe', 'pipe']})
  t.after(() => {if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')})
  await Promise.race([once(child.stdout, 'data'), once(child, 'error').then(([error]) => {throw error})])
  await stopChild(child)
  assert.ok(child.exitCode !== null || child.signalCode !== null)
  assert.equal(child.listenerCount('exit'), 0)
})
