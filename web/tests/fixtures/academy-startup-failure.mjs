import assert from 'node:assert/strict'
import * as childProcess from 'node:child_process'
import {readFile} from 'node:fs/promises'
import {SourceTextModule, SyntheticModule} from 'node:vm'

const scenario = new URL('../' + process.argv[2], import.meta.url)
const phase = process.argv[3]
assert.ok(['fixture-exit', 'browser-startup'].includes(phase))
const original = Error('Controlled browser startup failure before execution diagnostics')
original.browserInfrastructure = true
let backend, launches = 0
const source = new SourceTextModule(await readFile(scenario, 'utf8'), {
  identifier: scenario.href,
  initializeImportMeta: meta => {meta.url = scenario.href},
})
await source.link(async specifier => {
  const url = specifier.startsWith('.') ? new URL(specifier, scenario).href : specifier
  let exports = await import(url)
  if (specifier === 'node:child_process') {
    exports = {...exports, spawn: () => {
      assert.equal(backend, undefined, 'The scenario must start only one fixture')
      backend = childProcess.spawn(process.execPath, ['-e', phase === 'fixture-exit'
        ? 'process.stderr.write("controlled backend stderr\\n"); process.exit(19)'
        : 'console.log(JSON.stringify({port: 12345})); setInterval(() => {}, 1000)'],
      {stdio: ['ignore', 'pipe', 'pipe']})
      return backend
    }}
  } else if (specifier === './browser-scenario.mjs') {
    exports = {...exports, launchBrowser: async () => {launches++; throw original}}
  }
  return new SyntheticModule(Object.keys(exports), function () {
    for (const [name, value] of Object.entries(exports)) this.setExport(name, value)
  }, {identifier: url})
})
try {
  await assert.rejects(source.evaluate(), error => {
    if (phase === 'browser-startup') return error === original && error.browserInfrastructure === true
    assert.match(error.message, /fixture exited: 19controlled backend stderr/)
    return true
  })
  assert.equal(launches, phase === 'browser-startup' ? 1 : 0)
  assert.ok(backend && (backend.exitCode !== null || backend.signalCode !== null), 'The real fixture child must exit before the original failure returns')
  console.log('academy-startup-failure-preserved')
} finally {
  if (backend && backend.exitCode === null && backend.signalCode === null) {
    const exited = new Promise(resolve => backend.once('exit', resolve))
    backend.kill('SIGKILL')
    await exited
  }
}
