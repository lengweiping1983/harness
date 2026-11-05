import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {readFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {createContext, SourceTextModule, SyntheticModule} from 'node:vm'
import {createAcademySandboxCheck} from '../academy-environment.mjs'

const rejected = process.argv[2] === 'rejected'
const raw = {code: rejected ? 1 : 0, stderr: '', stdout: JSON.stringify({passed: !rejected,
  reason: rejected ? 'sandbox-probe-failed' : 'ready', backend: 'seatbelt', executable: 'controlled-python',
  execution: {status: rejected ? 'failed' : 'passed', exit_code: rejected ? 71 : 0,
    stdout: rejected ? '' : 'harness-academy-sandbox-ready\n', stderr: rejected ? 'original sandbox rejection' : '',
    truncated: false, memory_limited: false, sandbox: 'seatbelt'}})}
let probes = 0, drained = 0, closed = 0, disconnected = 0
let disconnect
const disconnectedDone = new Promise(resolve => {disconnect = resolve})
const check = createAcademySandboxCheck({probe: async () => {probes++; return raw}})
const packets = [], waiters = new Map()
const workerProcess = Object.assign(new EventEmitter(), {
  pid: process.pid, ppid: process.ppid, execPath: process.execPath, platform: process.platform, version: process.version,
  env: {HARNESS_TEST_BROWSER: 'controlled-browser'},
  chdir: directory => assert.equal(directory, 'controlled-web-root'),
  send: (packet, callback) => {packets.push(structuredClone(packet)); waiters.get(packet.type)?.(packets.at(-1)); callback()},
  disconnect: () => {disconnected++; disconnect()},
})
const runtime = {version: 'controlled-browser-version', stats: {viteStarts: 1},
  drainBrowsers: async () => {drained++}, close: async () => {closed++}}
const context = createContext({process: workerProcess, performance})
const synthetic = exports => new SyntheticModule(Object.keys(exports), function () {
  for (const [name, value] of Object.entries(exports)) this.setExport(name, value)
}, {context})
const file = new URL('../browser-worker.mjs', import.meta.url)
const source = new SourceTextModule(await readFile(file, 'utf8'), {
  context, identifier: file.href,
  initializeImportMeta: meta => {meta.dirname = fileURLToPath(new URL('./', file))},
  importModuleDynamically: async url => {
    assert.ok(url.endsWith('/controlled-smoke.mjs'))
    await check({sandbox: 'seatbelt'})
    const scenario = synthetic({})
    await scenario.link(assert.fail)
    await scenario.evaluate()
    return scenario
  },
})
await source.link(async specifier => {
  if (specifier === './academy-environment.mjs') return synthetic({requireAcademySandbox: check})
  if (specifier === './browser-runtime.mjs') return synthetic({webRoot: 'controlled-web-root',
    createBrowserRuntime: async options => {assert.equal(options.executablePath, 'controlled-browser'); return runtime},
    installBrowserRuntime: value => assert.equal(value, runtime)})
  return synthetic(await import(specifier))
})
await source.evaluate()
const ready = packets.find(packet => packet.type === 'ready')
assert.equal(ready.stats.academySandboxProbe, null)
assert.equal(probes, 0)
const request = (type, message) => new Promise(resolve => {
  waiters.set(type, resolve)
  workerProcess.emit('message', message)
})
const result = await request('result', {type: 'run', name: 'controlled-smoke.mjs'})
assert.equal(result.name, 'controlled-smoke.mjs')
assert.equal(Boolean(result.error?.infrastructure), rejected)
assert.deepEqual(result.stats.executionContext, {pid: process.pid, parentPid: process.ppid,
  executable: process.execPath, platform: process.platform, node: process.version})
assert.equal(result.stats.academySandboxProbe.status, 'completed')
assert.deepEqual(result.stats.academySandboxProbe.receipt, raw)
const shutdown = await request('closed', {type: 'close'})
await disconnectedDone
assert.deepEqual(shutdown.stats.academySandboxProbe, result.stats.academySandboxProbe)
assert.deepEqual(shutdown.stats.executionContext, result.stats.executionContext)
assert.deepEqual({probes, drained, closed, disconnected}, {probes: 1, drained: 1, closed: 1, disconnected: 1})
console.log('browser-worker-evidence-preserved')
