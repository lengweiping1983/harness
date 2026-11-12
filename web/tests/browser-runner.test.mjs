import assert from 'node:assert/strict'
import {test} from 'node:test'
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises'
import {createWriteStream, open as openFile} from 'node:fs'
import * as fs from 'node:fs'
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
    assert.deepEqual(inputIdentity(directory), dirty, 'An unchanged dirty tree still matches its recorded input identity')
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

test('real worker protocol preserves probe evidence and process identity through results and shutdown without launching browsers', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'harness-worker-test-'))
  let worker
  try {
    const file = path.join(directory, 'worker.mjs')
    await writeFile(file, `const executionContext = {pid: process.pid, parentPid: process.ppid, executable: process.execPath};
    let academySandboxProbe = null;
    const stats = () => ({viteStarts: 1, executionContext, academySandboxProbe});
    process.on('message', message => {
      if(message.type==='run') {
        academySandboxProbe = {status: 'completed', receipt: {code: message.name === 'rejected-smoke.mjs' ? 1 : 0,
          stdout: 'original raw sandbox receipt', stderr: ''}};
        process.send({type:'result',name:message.name,durationMs:1,stats:stats(),
          ...(academySandboxProbe.receipt.code ? {error: {message: 'controlled sandbox probe rejection', infrastructure: true}} : {})});
      } else {console.log('worker-close-confirmed');process.send({type:'closed',stats:stats()});process.disconnect()}
    });process.send({type:'ready',version:'fixture',stats:stats()});`)
    worker = await startWorker(0, {workerFile: file, executablePath: 'fixture', timeoutMs: 1000, logs: directory})
    assert.equal(worker.summary().academySandboxProbe, null)
    assert.equal(worker.summary().executionContext.parentPid, process.pid)
    assert.equal(worker.summary().executionContext.executable, process.execPath)
    assert.equal((await worker.run('original-smoke.mjs')).durationMs, 1)
    const success = worker.summary()
    assert.equal(success.academySandboxProbe.receipt.code, 0)
    success.academySandboxProbe.receipt.stdout = 'changed by consumer'
    assert.equal(worker.summary().academySandboxProbe.receipt.stdout, 'original raw sandbox receipt')
    const rejection = await worker.run('rejected-smoke.mjs')
    assert.equal(rejection.durationMs, 1)
    assert.ok(isInfrastructureError(rejection.error))
    const rejected = worker.summary()
    assert.equal(rejected.academySandboxProbe.receipt.code, 1)
    const closing = worker.close()
    assert.equal(worker.close(), closing, 'Concurrent waiters share the same process and log cleanup')
    await closing
    assert.match(await readFile(path.join(directory, 'worker-1.log'), 'utf8'), /worker-close-confirmed/)
    assert.deepEqual(worker.summary(), rejected, 'Shutdown must retain the actual probe receipt and original worker identity')
    worker = undefined
  } finally {await worker?.close(); await rm(directory, {recursive: true, force: true})}
})

test('worker run and close await delayed log opens before temporary files can be removed', {timeout: 10000}, async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'harness-worker-log-open-'))
  const releases = []
  let worker, reachedEnd
  const ending = new Promise(resolve => {reachedEnd = resolve})
  try {
    const file = path.join(directory, 'worker.mjs')
    await writeFile(file, `process.on('message', message => {
      if(message.type==='run') process.send({type:'result',name:message.name,durationMs:1});
      else {process.send({type:'closed'});process.disconnect()}
    });process.send({type:'ready',version:'fixture'});`)
    worker = await startWorker(0, {workerFile: file, executablePath: 'fixture', timeoutMs: 1000, logs: directory,
      createLogStream: file => {
        const output = createWriteStream(file, {fs: {...fs, open: (...args) => {releases.push(() => openFile(...args))}}})
        const end = output.end.bind(output)
        output.end = (...args) => {if (file.endsWith('delayed-smoke.mjs.log')) reachedEnd(); return end(...args)}
        return output
      }})
    let completed = false
    const running = worker.run('delayed-smoke.mjs').then(value => {completed = true; return value})
    await ending
    assert.equal(completed, false, 'A result must wait for its owned log to finish')
    for (const release of releases.splice(0)) release()
    await running
    assert.equal(await readFile(path.join(directory, 'delayed-smoke.mjs.log'), 'utf8'), '')
    await worker.close()
    assert.equal(await readFile(path.join(directory, 'worker-1.log'), 'utf8'), '')
    worker = undefined
  } finally {
    for (const release of releases.splice(0)) release()
    try {await worker?.close()} finally {await rm(directory, {recursive: true, force: true})}
  }
})

test('worker close propagates asynchronous log open errors after reaping its process', {timeout: 10000}, async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'harness-worker-log-error-'))
  let worker
  try {
    const file = path.join(directory, 'worker.mjs')
    await writeFile(file, `process.on('message', message => {
      if(message.type==='run') process.send({type:'result',name:message.name,durationMs:1});
      else {process.send({type:'closed'});process.disconnect()}
    });process.send({type:'ready',version:'fixture'});`)
    worker = await startWorker(0, {workerFile: file, executablePath: 'fixture', timeoutMs: 1000, logs: directory,
      createLogStream: file => createWriteStream(file.endsWith('worker-1.log') ? path.join(directory, 'absent', 'log') : file)})
    await worker.run('original-smoke.mjs')
    await assert.rejects(worker.close(), error => error.code === 'ENOENT')
    worker = undefined
  } finally {try {await worker?.close()} finally {await rm(directory, {recursive: true, force: true})}}
})

test('a hung worker is bounded and forcibly reaped rather than replayed', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'harness-worker-timeout-'))
  let worker
  try {
    const file = path.join(directory, 'worker.mjs')
    await writeFile(file, "process.on('message',()=>{});process.send({type:'ready',version:'fixture'});")
    worker = await startWorker(0, {workerFile: file, executablePath: 'fixture', timeoutMs: 10, logs: directory})
    await assert.rejects(worker.run('hung-smoke.mjs'), /timed out/)
    const closing = worker.close()
    assert.equal(worker.close(), closing)
    await closing
    assert.equal(await readFile(path.join(directory, 'worker-1.log'), 'utf8'), '')
    worker = undefined
  } finally {await worker?.close(); await rm(directory, {recursive: true, force: true})}
})
