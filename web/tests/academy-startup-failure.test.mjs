import assert from 'node:assert/strict'
import {execFile} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {promisify} from 'node:util'
import {test} from 'node:test'

for (const scenario of ['academy-editor-smoke.mjs', 'academy-tutor-smoke.mjs']) {
  for (const phase of ['fixture-exit', 'browser-startup']) {
    test(scenario + ' preserves ' + phase + ' failure through real backend exit before diagnostics initialize', async () => {
      const result = await promisify(execFile)(process.execPath, ['--experimental-vm-modules',
        fileURLToPath(new URL('./fixtures/academy-startup-failure.mjs', import.meta.url)), scenario, phase],
      {timeout: 30000, maxBuffer: 1024 * 1024})
      assert.match(result.stdout, /academy-startup-failure-preserved/)
      assert.doesNotMatch(result.stderr, /ReferenceError|before initialization/)
    })
  }
}
