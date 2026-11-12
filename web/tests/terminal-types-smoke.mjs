import {spawn} from 'node:child_process'
import {cleanupFixture} from './fixture-cleanup.mjs'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'
import {verifyTerminalTypes} from './terminal-types-scenarios.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
let diagnostics = '', browser, startupTimer, failure
backend.stderr.on('data', data => {diagnostics += data})
const lines = createInterface({input: backend.stdout})
try {
  const ready = await new Promise((resolve, reject) => {
    startupTimer = setTimeout(() => reject(new Error('Harness fixture startup timed out\n' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(new Error('Harness fixture exited: ' + code + '\n' + diagnostics)))
    lines.on('line', line => {
      try {const value = JSON.parse(line); if (Number.isInteger(value.port)) resolve(value)} catch { /* 启动诊断不是就绪回执。 */ }
    })
  })
  clearTimeout(startupTimer)
  browser = await launchBrowser()
  await verifyTerminalTypes(browser, 'http://127.0.0.1:' + ready.port)
} catch (error) {
  failure = error
} finally {
  clearTimeout(startupTimer)
  await cleanupFixture({error: failure, browser, lines, backend})
}
