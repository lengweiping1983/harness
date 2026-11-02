import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {test} from 'node:test'
import {DesktopUpdates} from '../updates.mjs'

function fixture(packaged = true) {
  const updater = new EventEmitter(), states = [], calls = []
  updater.checkForUpdates = async () => {
    calls.push('check')
    updater.emit('update-available', {version:'1.2.3'})
    return {updateInfo:{version:'1.2.3'}}
  }
  updater.downloadUpdate = async () => {calls.push('download'); return ['/cache/update.zip']}
  const updates = new DesktopUpdates({updater, packaged, notify:state => states.push(state)})
  return {updates, updater, states, calls}
}

test('development never checks or downloads; stable updates disable downgrade and automatic download', async () => {
  const {updates, updater, calls} = fixture(false)
  assert.equal((await updates.check()).state, 'development')
  await assert.rejects(updates.download())
  assert.deepEqual(calls, [])
  assert.equal(updater.autoDownload, false)
  assert.equal(updater.allowPrerelease, false)
  assert.equal(updater.allowDowngrade, false)
})

test('checks share one request, download cannot masquerade as a successful check', async () => {
  const {updates, updater, calls} = fixture()
  let release
  updater.checkForUpdates = () => new Promise(resolve => {release = resolve})
  const first = updates.check()
  assert.equal(updates.check(), first)
  await assert.rejects(updates.download(), /still in progress/)
  updater.emit('update-not-available')
  release({updateInfo:{version:'1.0.0'}})
  assert.equal((await first).state, 'current')
  assert.deepEqual(calls, [])
})

test('macOS early downloaded event does not confirm native validation; progress and downloaded cache are retained', async () => {
  const {updates, updater, states, calls} = fixture()
  await updates.check()
  let finish
  updater.downloadUpdate = () => new Promise(resolve => {finish = resolve})
  const first = updates.download()
  assert.equal(updates.download(), first)
  assert.equal(updates.check(), first)
  await Promise.resolve()
  updater.emit('download-progress', {percent:42.8})
  assert.equal(updates.progress, 42)
  updater.emit('update-downloaded')
  assert.equal(updates.state, 'downloading')
  finish(['/cache/update.zip'])
  assert.deepEqual(await first, {state:'downloaded', version:'1.2.3', progress:100})
  assert.equal((await updates.check()).state, 'downloaded')
  assert.deepEqual(calls, ['check'])
  assert.deepEqual(states, ['checking','available','downloading','downloading','downloaded'])
})

test('native errors and empty results cannot report success, and explicit retry rechecks the release', async () => {
  for (const mode of ['throw','event','empty']) {
    const {updates, updater} = fixture()
    await updates.check()
    const original = Error('signature/network failure')
    updater.downloadUpdate = () => {
      updater.emit('update-downloaded')
      if (mode === 'throw') throw original
      if (mode === 'event') updater.emit('error', original)
      return Promise.resolve([])
    }
    await assert.rejects(updates.download(), mode === 'empty' ? /confirmed files/ : error => error === original)
    assert.equal(updates.state, 'error')
    assert.equal(updates.pending, null)
    assert.equal((await updates.check()).state, 'available')
  }
})

test('missing release confirmation and synchronous check failure do not leave the operation stuck', async () => {
  const {updates, updater} = fixture()
  updater.checkForUpdates = () => ({updateInfo:{version:'1.2.3'}})
  await assert.rejects(updates.check(), /confirmed release/)
  const original = Error('synchronous failure')
  updater.checkForUpdates = () => {throw original}
  await assert.rejects(updates.check(), error => error === original)
  assert.equal(updates.pending, null)
})


test('macOS waits for native signature validation after download resolves, releases listeners on both outcomes', async () => {
  for (const success of [true, false]) {
    const {updater} = fixture()
    const nativeUpdater = new EventEmitter()
    const updates = new DesktopUpdates({updater, nativeUpdater, packaged:true})
    await updates.check()
    let transferred
    const transfer = new Promise(resolve => {transferred = resolve})
    updater.downloadUpdate = async () => {transferred(); return ['/cache/update.zip']}
    const pending = updates.download()
    await transfer
    assert.equal(updates.state, 'downloading')
    assert.equal(nativeUpdater.listenerCount('update-downloaded'), 1)
    if (success) {
      nativeUpdater.emit('update-downloaded')
      assert.equal((await pending).state, 'downloaded')
    } else {
      const original = Error('native signature rejected')
      nativeUpdater.emit('error', original)
      await assert.rejects(pending, error => error === original)
      assert.equal(updates.state, 'error')
    }
    assert.equal(nativeUpdater.listenerCount('update-downloaded'), 0)
    assert.equal(nativeUpdater.listenerCount('error'), 0)
  }
})
