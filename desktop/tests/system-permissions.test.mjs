import assert from 'node:assert/strict'
import {test} from 'node:test'
import {SystemPermissions, screenshotReply} from '../system-permissions.mjs'
import {ScreenshotSession} from '../screenshot.mjs'

function fixture(platform = 'darwin') {
  let status = 'not-determined', granted = false
  const calls = []
  const permissions = new SystemPermissions({platform,
    systemPreferences:{getMediaAccessStatus:type => {calls.push(['status',type]); return status},
      askForMediaAccess:async type => {calls.push(['ask',type]); status = granted ? 'granted' : 'denied'; return granted},
      isTrustedAccessibilityClient:() => granted},
    desktopCapturer:{getSources:async options => {calls.push(['sources',options]); status = granted ? 'granted' : 'denied'; return []}},
    shell:{openExternal:async url => calls.push(['settings',url])}})
  return {permissions, calls, status:value => {status = value}, grant:value => {granted = value}}
}

test('screen denial is followed by settings on the next click and rechecked after granting', async () => {
  const f = fixture()
  assert.deepEqual(await f.permissions.request('screen'), {granted:false, copy:{key:'ui.systemPermission.screen.request'}})
  assert.deepEqual(await f.permissions.request('screen'), {granted:false, copy:{key:'ui.systemPermission.screen.settings'}})
  assert.equal(f.calls.filter(([kind]) => kind === 'sources').length, 1)
  assert.deepEqual(f.calls.filter(([kind]) => kind === 'settings'), [['settings','x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture']])
  f.status('granted')
  assert.deepEqual(await f.permissions.request('screen'), {granted:true})
  f.status('denied')
  await f.permissions.request('screen')
  assert.equal(f.calls.filter(([kind]) => kind === 'settings').length, 2)
})

test('microphone and camera use a single system prompt, then the exact settings pane', async () => {
  for (const type of ['microphone','camera']) {
    const f = fixture()
    const first = f.permissions.request(type)
    assert.equal(first, f.permissions.request(type))
    assert.equal((await first).granted, false)
    assert.equal((await f.permissions.request(type)).copy.key, `ui.systemPermission.${type}.settings`)
    assert.equal(f.calls.filter(([kind]) => kind === 'ask').length, 1)
    assert.equal(f.calls.at(-1)[1], `x-apple.systempreferences:com.apple.preference.security?Privacy_${type === 'microphone' ? 'Microphone' : 'Camera'}`)
    f.status('granted')
    assert.deepEqual(await f.permissions.request(type), {granted:true})
  }
})

test('Windows device denial opens privacy settings and accessibility is rechecked', async () => {
  for (const type of ['microphone','camera']) {
    const f = fixture('win32'); f.status('denied')
    assert.equal((await f.permissions.request(type)).granted, false)
    assert.equal(f.calls.at(-1)[1], `ms-settings:privacy-${type === 'camera' ? 'webcam' : 'microphone'}`)
    assert.ok(!f.calls.some(([kind]) => kind === 'ask'))
  }
  const f = fixture()
  assert.equal((await f.permissions.request('accessibility')).granted, false)
  f.grant(true)
  assert.deepEqual(await f.permissions.request('accessibility'), {granted:true})
})

test('permission preflight never hides or captures a denied screen and allows another click', async () => {
  const f = fixture(); f.status('denied')
  const session = new ScreenshotSession({window:{isDestroyed:() => false}, authorize:() => f.permissions.request('screen')})
  for (let index = 0; index < 2; index++) {
    assert.deepEqual(await session.capture(true), {permission:{key:'ui.systemPermission.screen.settings'}})
    assert.equal(session.active, false)
    session.finish()
  }
  assert.equal(f.calls.filter(([kind]) => kind === 'settings').length, 2)
})

test('IPC screenshot failures retain resource identity instead of leaking command errors', async t => {
  t.mock.method(console, 'error', () => {})
  const primary = Error('Command failed: /private/fixture')
  assert.deepEqual(await screenshotReply(() => {throw primary}, 'capture'), {failure:{key:'ui.screenshot.captureFailed'}})
  assert.deepEqual(await screenshotReply(() => {throw primary}, 'finish'), {failure:{key:'ui.screenshot.cleanupUnknown'}})
  const f = fixture()
  f.permissions.shell.openExternal = async () => {throw primary}
  f.status('denied')
  assert.deepEqual(await screenshotReply(() => f.permissions.request('screen'), 'capture'), {failure:{key:'ui.systemPermission.requestFailed'}})
})

test('late screen authorization after finish cannot capture or hide the replacement window', async () => {
  let resolve
  const attempts = []
  const session = new ScreenshotSession({window:{isDestroyed:() => false},
    authorize:() => new Promise(done => {resolve = done}), releaseImage:() => attempts.push('image'), resume:() => attempts.push('resume')})
  const capture = session.capture(true)
  session.finish()
  resolve({granted:true})
  assert.equal(await capture, null)
  assert.deepEqual(attempts, ['image','resume'])
})
