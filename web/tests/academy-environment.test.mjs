import assert from 'node:assert/strict'
import {test} from 'node:test'
import {createAcademySandboxCheck} from './academy-environment.mjs'
import {isInfrastructureError, runScenarios} from './browser-runner.mjs'

const execution = {status: 'passed', exit_code: 0, stdout: 'harness-academy-sandbox-ready\n', stderr: '',
  truncated: false, memory_limited: false, sandbox: 'seatbelt'}
function receipt(changes = {}, code = 0, stderr = '') {
  return {code, stdout: JSON.stringify({passed: true, backend: 'seatbelt', reason: 'ready', execution, ...changes}), stderr}
}
const infrastructure = error => isInfrastructureError({infrastructure: error.browserInfrastructure})
const requireAcademySandbox = (project, options) => createAcademySandboxCheck(options)(project)

test('an explicit unavailable sandbox blocks execution as infrastructure without accepting malformed project evidence', async () => {
  const probe = assert.fail
  await assert.rejects(() => requireAcademySandbox({sandbox: 'unavailable'}, {probe}), infrastructure)
  for (const project of [null, {}, {sandbox: null}, {sandbox: 'windows'}, {sandbox: true}]) {
    await assert.rejects(() => requireAcademySandbox(project, {probe}), error => error instanceof assert.AssertionError && !infrastructure(error))
  }
})

test('an installed backend must await a real launch receipt before allowing course execution', async () => {
  for (const sandbox of ['seatbelt', 'bubblewrap']) {
    let release, completed = false, calls = 0
    const waiting = new Promise(resolve => {release = resolve})
    const result = receipt({backend: sandbox, execution: {...execution, sandbox}})
    const checking = requireAcademySandbox({sandbox}, {probe: () => {calls++; return waiting}}).then(() => {completed = true})
    await Promise.resolve()
    assert.equal(calls, 1)
    assert.equal(completed, false)
    release(result)
    await checking
    assert.equal(completed, true)
  }
})

test('a fixed probe rejection is infrastructure evidence and preserves the complete raw receipt', async () => {
  for (const diagnostic of ['sandbox-exec: sandbox_apply: Operation not permitted\n', 'unrelated launcher diagnostic\n']) {
    const raw = receipt({passed: false, reason: 'sandbox-probe-failed',
      execution: {...execution, status: 'failed', exit_code: 71, stdout: '', stderr: diagnostic}}, 1)
    let calls = 0
    await assert.rejects(() => requireAcademySandbox({sandbox: 'seatbelt'}, {probe: async () => {calls++; return raw}}), error => {
      assert.ok(infrastructure(error))
      assert.equal(error.probe, raw)
      assert.ok(error.message.includes(raw.stdout))
      return true
    })
    assert.equal(calls, 1, 'A failed probe must never retry or execute the lesson')
  }
})

test('a timed out fixed probe cannot allow execution or be reported as success', async () => {
  const raw = receipt({passed: false, reason: 'sandbox-probe-failed', execution: {...execution, status: 'timeout', exit_code: -9}}, 1)
  await assert.rejects(() => requireAcademySandbox({sandbox: 'seatbelt'}, {probe: async () => raw}), infrastructure)
})

test('probe evidence records the actual launch once and returns isolated snapshots for success and rejection', async () => {
  for (const raw of [receipt(), receipt({passed: false, reason: 'sandbox-probe-failed',
    execution: {...execution, status: 'failed', exit_code: 71, stdout: '', stderr: 'original rejection'}}, 1)]) {
    let release, calls = 0
    const check = createAcademySandboxCheck({probe: () => {calls++; return new Promise(resolve => {release = resolve})}})
    assert.equal(check.snapshot(), null, 'Reading evidence must not launch the probe')
    const waiting = Promise.allSettled([check({sandbox: 'seatbelt'}), check({sandbox: 'seatbelt'})])
    await Promise.resolve()
    const running = check.snapshot()
    assert.equal(running.status, 'running')
    assert.ok(Number.isFinite(Date.parse(running.started)))
    assert.equal(running.finished, undefined)
    release(raw)
    const results = await waiting
    assert.deepEqual(results.map(result => result.status), [raw.code ? 'rejected' : 'fulfilled', raw.code ? 'rejected' : 'fulfilled'])
    const finished = check.snapshot()
    assert.equal(finished.status, 'completed', 'A nonzero process receipt is still a completed probe, not a passing probe')
    assert.equal(finished.started, running.started)
    assert.ok(Date.parse(finished.finished) >= Date.parse(finished.started))
    assert.deepEqual(finished.receipt, raw)
    if (raw.code) assert.equal(results[0].reason.probe, raw)
    const original = structuredClone(finished)
    finished.receipt.stdout = 'changed by consumer'
    raw.stderr = 'changed by producer'
    assert.deepEqual(check.snapshot(), original)
    await Promise.allSettled([check({sandbox: 'seatbelt'})])
    assert.equal(calls, 1, 'Inspecting or later consuming evidence must not relaunch the probe')
    assert.deepEqual(check.snapshot(), original)
  }
})

test('probe evidence distinguishes a launch exception and unavailable backend without inventing execution receipts', async () => {
  const original = Error('original process supervision failure')
  let calls = 0
  const check = createAcademySandboxCheck({probe: async () => {calls++; throw original}})
  await assert.rejects(check({sandbox: 'unavailable'}), infrastructure)
  assert.equal(check.snapshot(), null)
  assert.equal(calls, 0)
  await assert.rejects(check({sandbox: 'seatbelt'}), error => error === original)
  const evidence = check.snapshot()
  assert.equal(evidence.status, 'error')
  assert.equal(evidence.error.message, original.message)
  assert.equal(evidence.error.stack, original.stack)
  assert.equal(evidence.receipt, undefined)
  evidence.error.message = 'changed by consumer'
  assert.equal(check.snapshot().error.message, original.message)
  await assert.rejects(check({sandbox: 'seatbelt'}), error => error === original)
  assert.equal(calls, 1)
})

test('one worker shares a single probe across scenes and concurrent waiters, including a failed probe', async () => {
  for (const raw of [receipt(), receipt({passed: false, reason: 'sandbox-probe-failed',
    execution: {...execution, status: 'failed', exit_code: 71, stdout: '', stderr: 'original rejection'}}, 1)]) {
    let calls = 0, release
    const probe = () => {calls++; return new Promise(resolve => {release = resolve})}
    const check = createAcademySandboxCheck({probe})
    const waiting = Promise.allSettled([check({sandbox: 'seatbelt'}), check({sandbox: 'seatbelt'})])
    await Promise.resolve()
    assert.equal(calls, 1)
    release(raw)
    const results = await waiting
    const status = raw.code === 0 ? 'fulfilled' : 'rejected'
    assert.deepEqual(results.map(result => result.status), [status, status])
    const [later] = await Promise.allSettled([check({sandbox: 'seatbelt'})])
    assert.equal(later.status, status)
    assert.equal(calls, 1, 'A later scene must observe the same preflight without retrying it')
    if (status === 'rejected') assert.equal(later.reason.probe, raw)
    const nextWorker = createAcademySandboxCheck({probe: async () => {calls++; return receipt()}})
    await nextWorker({sandbox: 'seatbelt'})
    assert.equal(calls, 2, 'A new worker must run its own preflight')
  }
})

test('the runner stops scheduling after a launch probe fails and leaves remaining scenes unverified', async () => {
  const calls = [], raw = receipt({passed: false, reason: 'sandbox-probe-failed',
    execution: {...execution, status: 'failed', exit_code: 71, stdout: '', stderr: 'original sandbox rejection'}}, 1)
  const report = await runScenarios(['execution-scene', 'later-scene'], {jobs: 1, createWorker: async () => ({
    async run(name) {
      calls.push(name)
      try {await requireAcademySandbox({sandbox: 'seatbelt'}, {probe: async () => raw})}
      catch (error) {return {error: {message: error.message, infrastructure: error.browserInfrastructure}}}
      assert.fail('The failed fixed probe must not admit the lesson')
    },
    async close() {calls.push('close')},
    summary() {return {}},
  })})
  assert.deepEqual(calls, ['execution-scene', 'close'])
  assert.deepEqual(report.results.map(result => result.status), ['infrastructure_failed', 'not_run'])
  assert.equal(report.infrastructure.length, 1)
  assert.ok(report.infrastructure[0].message.includes(raw.stdout))
})

test('malformed or inconsistent probe evidence remains a validation failure', async () => {
  const invalid = [
    {code: 0, stdout: 'invalid JSON', stderr: ''}, receipt({passed: 'true'}), receipt({backend: 'bubblewrap'}),
    receipt({}, 1), receipt({}, 0, 'unexpected diagnostic'), receipt({execution: null}),
    receipt({execution: {...execution, exit_code: false}}), receipt({execution: {...execution, stdout: ''}}),
    receipt({execution: {...execution, truncated: true}}), receipt({execution: {...execution, memory_limited: true}}),
    receipt({execution: {...execution, sandbox: 'bubblewrap'}}), receipt({reason: 'sandbox-probe-failed'}),
    receipt({passed: false, reason: 'sandbox-probe-failed'}, 1),
  ]
  for (const raw of invalid) {
    await assert.rejects(() => requireAcademySandbox({sandbox: 'seatbelt'}, {probe: async () => raw}), error => !infrastructure(error))
  }
})

test('probe implementation and process failures are not relabeled as sandbox permission failures', async () => {
  const original = Error('original probe process failure')
  await assert.rejects(() => requireAcademySandbox({sandbox: 'seatbelt'}, {probe: async () => {throw original}}), error => error === original)
  const raw = receipt({passed: false, reason: 'sandbox-probe-error', error: {type: 'RuntimeError', message: 'original supervisor failure'}}, 1, 'original traceback')
  await assert.rejects(() => requireAcademySandbox({sandbox: 'seatbelt'}, {probe: async () => raw}), error => {
    assert.ok(!infrastructure(error))
    assert.equal(error.probe, raw)
    assert.ok(error.message.includes('original supervisor failure'))
    assert.ok(error.message.includes('original traceback'))
    return true
  })
})
