import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {mkdtemp, rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {createRequire} from 'node:module'
import {fileURLToPath} from 'node:url'

const profile = await mkdtemp(path.join(tmpdir(), 'harness-menu-quit-smoke-'))
const env = {...process.env}
delete env.ELECTRON_RUN_AS_NODE
try {
  for (const [index, language] of ['en', 'zh-CN', 'en'].entries()) {
    const child = spawn(createRequire(import.meta.url)('electron'), [
      fileURLToPath(new URL('./fixtures/menu-quit-app.mjs', import.meta.url)),
      path.join(profile, String(index)), language,
    ], {env, stdio:['ignore', 'pipe', 'pipe']})
    let stdout = '', stderr = ''
    child.stdout.on('data', chunk => {stdout += chunk})
    child.stderr.on('data', chunk => {stderr += chunk})
    const timeout = setTimeout(() => child.kill('SIGKILL'), 15000)
    try {
      const result = await new Promise((resolve, reject) => {
        child.once('error', reject)
        child.once('close', (code, signal) => resolve({code, signal}))
      })
      assert.deepEqual(result, {code:0, signal:null}, `${language}: ${stdout}\n${stderr}`)
      assert.match(stdout, /menu-quit-complete:1:1/)
      console.log(`Native menu quit passed: ${language}; window closed, listeners released, cleanup completed once.`)
    } finally {
      clearTimeout(timeout)
    }
  }
} finally {
  await rm(profile, {recursive:true, force:true})
}
