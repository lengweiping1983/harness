import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {mkdtemp, rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {createRequire} from 'node:module'
import {fileURLToPath} from 'node:url'

assert.equal(process.platform, 'darwin', 'Native About smoke requires macOS')
const profile = await mkdtemp(path.join(tmpdir(), 'harness-about-smoke-'))
const env = {...process.env}
delete env.ELECTRON_RUN_AS_NODE
const child = spawn(createRequire(import.meta.url)('electron'), [
  fileURLToPath(new URL('./fixtures/about-panel-app.mjs', import.meta.url)), profile,
], {env, stdio: ['ignore', 'pipe', 'pipe']})
let stdout = '', stderr = ''
child.stdout.on('data', chunk => { stdout += chunk; process.stdout.write(chunk) })
child.stderr.on('data', chunk => { stderr += chunk })
const timeout = setTimeout(() => child.kill('SIGKILL'), 15000)
try {
  const result = await new Promise((resolve, reject) => {
    child.once('error', reject)
    child.once('close', (code, signal) => resolve({code, signal}))
  })
  assert.deepEqual(result, {code: 0, signal: null}, stderr)
  assert.match(stdout, /native-about-opened:3/)
  console.log('Native About menu smoke passed: English → Chinese → English; process remained alive.')
} finally {
  clearTimeout(timeout)
  await rm(profile, {recursive: true, force: true})
}
