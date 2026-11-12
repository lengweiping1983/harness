import assert from 'node:assert/strict'
import {execFile} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {promisify} from 'node:util'
import {test} from 'node:test'

for (const outcome of ['passed', 'rejected']) {
  test('actual worker reports ' + outcome + ' sandbox evidence through result and close without launching browsers', async () => {
    const result = await promisify(execFile)(process.execPath, ['--experimental-vm-modules',
      fileURLToPath(new URL('./fixtures/browser-worker-evidence.mjs', import.meta.url)), outcome],
    {timeout: 30000, maxBuffer: 1024 * 1024})
    assert.match(result.stdout, /browser-worker-evidence-preserved/)
  })
}
