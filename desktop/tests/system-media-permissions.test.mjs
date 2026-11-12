import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {test} from 'node:test'
import {createBrowserRuntime} from '../browser-runtime.mjs'
import {DesktopVoice} from '../voice.mjs'

async function fixture(t) {
  t.mock.method(console, 'warn', () => {})
  const handlers = new Map(), ipcMain = new EventEmitter(), browserSession = new EventEmitter()
  ipcMain.handle = (name, callback) => handlers.set(name, callback)
  browserSession.setPermissionCheckHandler = callback => {browserSession.check = callback}
  browserSession.setPermissionRequestHandler = callback => {browserSession.request = callback}
  const views = [], published = []
  class View {
    constructor() {
      const wc = this.webContents = new EventEmitter()
      wc.id = views.length + 1
      let destroyed = false
      Object.assign(wc, {isDestroyed:() => destroyed, getURL:() => 'https://example.com/', getTitle:() => 'fixture', isLoading:() => false,
        getZoomFactor:() => 1, navigationHistory:{canGoBack:() => false, canGoForward:() => false}, setWindowOpenHandler() {},
        debugger:{attach() {}, detach() {}, isAttached:() => true, sendCommand:async () => ({targetInfo:{targetId:'target-' + wc.id}})},
        loadURL:async () => {}, focus() {}, stopFindInPage() {}, close:() => {destroyed = true; wc.emit('destroyed')}})
      views.push(this)
    }
    getBounds() {return {x:0,y:0,width:1024,height:768}}
    setBounds() {}
    setVisible() {}
  }
  const window = new EventEmitter()
  Object.assign(window, {isDestroyed:() => false, getContentSize:() => [1200,800], contentView:{addChildView() {},removeChildView() {}}, webContents:new EventEmitter()})
  Object.assign(window.webContents, {mainFrame:{url:'http://localhost:1234/'}, focus() {}, send:(...args) => published.push(args)})
  const systemPermissions = {status:() => 'denied', request:t.mock.fn(async () => ({granted:false, copy:{key:'ui.systemPermission.microphone.settings'}}))}
  const runtime = createBrowserRuntime({WebContentsView:View, ipcMain, session:{fromPartition:() => browserSession}, getWindow:() => window, getDownloads:() => undefined, getOrigin:() => 'http://localhost:1234', systemPermissions})
  runtime.attach()
  const action = (name, args = {}) => handlers.get('browser')({sender:window.webContents, senderFrame:window.webContents.mainFrame}, name, args)
  await action('bind', {session_id:'first'})
  await action('layout', {visible:true,bounds:{x:0,y:0,width:500,height:300}})
  await action('new_tab', {url:'https://example.com/'})
  t.after(() => runtime.close())
  return {action,browserSession,systemPermissions,published,wc:views[0].webContents}
}

test('website consent precedes system device access and a rejected system request can be retried', async t => {
  const f = await fixture(t), callbacks = []
  const details = {requestingUrl:'https://example.com/',mediaTypes:['audio']}
  f.browserSession.request(f.wc,'media',allow => callbacks.push(allow),details)
  assert.equal(f.systemPermissions.request.mock.callCount(),0)
  const request = (await f.action('state')).permissions.requests[0]
  await f.action('permission_answer',{id:request.id,allow:false})
  assert.deepEqual(callbacks,[false])
  assert.equal(f.systemPermissions.request.mock.callCount(),0)
  f.browserSession.request(f.wc,'media',allow => callbacks.push(allow),details)
  await f.action('permission_answer',{id:(await f.action('state')).permissions.requests[0].id,allow:true})
  await new Promise(setImmediate)
  assert.deepEqual(callbacks,[false,false])
  assert.equal(f.published.at(-1)[0],'browser-error')
  assert.deepEqual(f.published.at(-1)[1].copy,{key:'ui.systemPermission.microphone.settings'})
  assert.equal(f.browserSession.check(f.wc,'media','https://example.com/',details),false)
  f.systemPermissions.request.mock.mockImplementation(async () => ({granted:true}))
  f.browserSession.request(f.wc,'media',allow => callbacks.push(allow),details)
  await new Promise(setImmediate)
  assert.deepEqual(callbacks,[false,false,true])
  assert.equal(f.systemPermissions.request.mock.callCount(),2)
})

test('system media grants are discarded after navigation, conversation round-trips and hiding', async t => {
  for (const mode of ['navigate','round-trip','hide','reset']) {
    await t.test(mode, async t => {
      const f = await fixture(t), callbacks = []
      let resolve
      f.systemPermissions.request.mock.mockImplementation(() => new Promise(done => {resolve = done}))
      f.browserSession.request(f.wc,'media',allow => callbacks.push(allow),{requestingUrl:'https://example.com/',mediaTypes:['audio']})
      await f.action('permission_answer',{id:(await f.action('state')).permissions.requests[0].id,allow:true})
      if (mode === 'navigate') f.wc.emit('did-start-navigation',{},'https://example.com/next',false,true)
      if (mode === 'round-trip') {await f.action('bind',{session_id:'second'}); await f.action('bind',{session_id:'first'})}
      if (mode === 'hide') {await f.action('layout',{visible:false,bounds:{x:0,y:0,width:500,height:300}}); await f.action('layout',{visible:true,bounds:{x:0,y:0,width:500,height:300}})}
      if (mode === 'reset') {f.wc.reload = () => {}; await f.action('permission_reset')}
      resolve({granted:true})
      await new Promise(setImmediate)
      assert.deepEqual(callbacks,[false])
      assert.ok(!f.published.some(([kind]) => kind === 'browser-error'))
    })
  }
})

test('global dictation opens accessibility authorization before reading or recording the target', async () => {
  let requests = 0, targets = 0
  const voice = new DesktopVoice({app:{getPath:() => '/fixture'},getWindow:() => ({isFocused:() => false}),
    systemPermissions:{request:async capability => {requests++; assert.equal(capability,'accessibility'); return {granted:false,copy:{key:'ui.systemPermission.accessibility.settings'}}}}})
  voice.registered = 'Control+Alt+Space'
  voice.native = async () => {targets++; return '123'}
  for (let index = 0; index < 2; index++) await assert.rejects(voice.toggle(),error => error.copy.key === 'ui.systemPermission.accessibility.settings')
  assert.equal(requests,2)
  assert.equal(targets,0)
  assert.equal(voice.active,null)
})
