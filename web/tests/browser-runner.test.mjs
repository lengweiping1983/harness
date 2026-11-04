import assert from 'node:assert/strict'
import {test} from 'node:test'
import {mkdtemp, rm, writeFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {execFileSync} from 'node:child_process'
import {mkdir} from 'node:fs/promises'
import {browserOptions, inputIdentity, isInfrastructureError, runScenarios, startWorker} from './browser-runner.mjs'

const available = ['a-smoke.mjs', 'b-smoke.mjs', 'c-smoke.mjs']

test('input identity distinguishes a dirty precommit baseline from identical committed contents', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'harness-browser-identity-'))
  const git = (...args) => execFileSync('git', args, {cwd: directory, encoding: 'utf8'})
  try {
    git('init', '-q'); git('config', 'user.name', 'fixture'); git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'core.autocrlf', 'false'); git('config', 'core.eol', 'lf')
    await mkdir(path.join(directory, '.git', 'no-hooks'))
    const commit = message => git('-c', 'core.hooksPath=' + path.join(directory, '.git', 'no-hooks'),
      '-c', 'commit.gpgsign=false', 'commit', '-qm', message)
    await writeFile(path.join(directory, 'scene.mjs'), 'before\n')
    git('add', '.'); commit('baseline')
    const clean = inputIdentity(directory)
    assert.deepEqual(clean.changed, [])
    await writeFile(path.join(directory, 'scene.mjs'), 'after\n')
    await writeFile(path.join(directory, 'untracked.mjs'), 'untracked\n')
    const dirty = inputIdentity(directory)
    assert.equal(dirty.revision, clean.revision)
    assert.deepEqual(dirty.changed, ['scene.mjs', 'untracked.mjs'])
    assert.notEqual(dirty.fingerprint, clean.fingerprint)
    git('add', '.'); commit('same contents')
    const committed = inputIdentity(directory)
    assert.notEqual(committed.revision, dirty.revision)
    assert.notEqual(committed.fingerprint, dirty.fingerprint)
    assert.deepEqual(committed.changed, [])
    // 任意层级的研究目录都不读取正文，也不改变已核实输入身份。
    await mkdir(path.join(directory, 'nested', 'research'), {recursive: true})
    await writeFile(path.join(directory, 'nested', 'research', 'ignored.txt'), 'excluded\n')
    assert.deepEqual(inputIdentity(directory), committed)
  } finally {await rm(directory, {recursive: true, force: true})}
})

test('runner controls preserve complete selections and shards, rejecting malformed or duplicate limits', () => {
  assert.deepEqual(browserOptions(available, ['--jobs=2', '--shard=1/2']).scenarios, ['a-smoke.mjs', 'c-smoke.mjs'])
  assert.equal(browserOptions(available, []).jobs, 1)
  for (const args of [['--jobs=0'], ['--jobs=9'], ['--jobs=1.5'], ['--jobs=1', '--jobs=2'],
    ['--timeout=0'], ['--timeout=3601'], ['--report='], ['--unknown'], ['--shard=1/2', available[0]]]) {
    assert.throws(() => browserOptions(available, args))
  }
})

test('bounded workers execute every scenario exactly once and keep collecting business failures', async () => {
  const names = Array.from({length: 13}, (_, i) => `scene-${i}`), calls = [], closed = []
  let active = 0, maximum = 0
  const outcome = await runScenarios(names, {jobs: 3, createWorker: async index => ({
    async run(name) {
      calls.push(name); maximum = Math.max(maximum, ++active)
      await new Promise(resolve => setImmediate(resolve)); active--
      return name === names[4] ? {error: {message: 'original assertion'}} : {}
    }, close: async () => {closed.push(index)}, summary: () => ({index}),
  })})
  assert.deepEqual([...calls].sort(), [...names].sort())
  assert.equal(maximum, 3); assert.equal(closed.length, 3)
  assert.equal(outcome.results.filter(row => row.status === 'failed').length, 1)
  assert.equal(outcome.results.filter(row => row.status === 'passed').length, 12)
  assert.deepEqual(outcome.infrastructure, [])
})

test('shared preparation failure stops new work and retains unexecuted scenarios separately', async () => {
  let calls = 0, closed = false
  const original = {message: 'cleanup aggregate', errors: [{message: 'assertion'}, {message: 'cache denied', infrastructure: true}]}
  assert.ok(isInfrastructureError(original))
  assert.equal(isInfrastructureError({message: 'SAFE_DELETE_BULK_CONFIRM_REQUIRED in user text'}), false)
  const outcome = await runScenarios(available, {jobs: 1, createWorker: async () => ({
    run: async () => {calls++; return {error: original}}, close: async () => {closed = true}, summary: () => ({}),
  })})
  assert.equal(calls, 1); assert.ok(closed)
  assert.deepEqual(outcome.results.map(row => row.status), ['infrastructure_failed', 'not_run', 'not_run'])
  assert.equal(outcome.results[0].error, original)
})

test('preflight, transport and shutdown errors cannot produce a passing suite', async () => {
  const preflight = await runScenarios(available, {jobs: 2, createWorker: async () => {throw Error('browser unavailable')}})
  assert.ok(preflight.results.every(row => row.status === 'not_run'))
  assert.equal(preflight.infrastructure.length, 2)
  const crashed = await runScenarios(available, {jobs: 1, createWorker: async () => ({
    run: async () => {throw Error('worker crashed')}, close: async () => {throw Error('cleanup unconfirmed')}, summary: () => ({}),
  })})
  assert.equal(crashed.results[0].status, 'infrastructure_failed')
  assert.deepEqual(crashed.infrastructure.map(error => error.message), ['worker crashed', 'cleanup unconfirmed'])
})

test('real worker protocol keeps results paired and drains a normal worker without launching browsers', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'harness-worker-test-'))
  let worker
  try {
    const file = path.join(directory, 'worker.mjs')
    await writeFile(file, `process.on('message', message => {
      if(message.type==='run') process.send({type:'result',name:message.name,durationMs:1});
      else {process.send({type:'closed',stats:{viteStarts:1}});process.disconnect()}
    });process.send({type:'ready',version:'fixture'});`)
    worker = await startWorker(0, {workerFile: file, executablePath: 'fixture', timeoutMs: 1000, logs: directory})
    assert.equal((await worker.run('original-smoke.mjs')).durationMs, 1)
    await worker.close(); worker = undefined
  } finally {await worker?.close(); await rm(directory, {recursive: true, force: true})}
})

test('a hung worker is bounded and forcibly reaped rather than replayed', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'harness-worker-timeout-'))
  let worker
  try {
    const file = path.join(directory, 'worker.mjs')
    await writeFile(file, "process.on('message',()=>{});process.send({type:'ready',version:'fixture'});")
    worker = await startWorker(0, {workerFile: file, executablePath: 'fixture', timeoutMs: 10, logs: directory})
    await assert.rejects(worker.run('hung-smoke.mjs'), /timed out/)
    await worker.close(); worker = undefined
  } finally {await worker?.close(); await rm(directory, {recursive: true, force: true})}
})
