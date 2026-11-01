import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {test} from 'node:test'
import {createBrowserRuntime} from '../browser-runtime.mjs'

test('browser runtime registers trusted IPC and preserves session control isolation', async () => {
  const ipcMain = new EventEmitter()
  const handlers = new Map()
  ipcMain.handle = (name, handler) => handlers.set(name, handler)
  const browserSession = new EventEmitter()
  browserSession.setPermissionCheckHandler = handler => {browserSession.check = handler}
  browserSession.setPermissionRequestHandler = handler => {browserSession.request = handler}
  const window = new EventEmitter()
  window.webContents = new EventEmitter()
  window.webContents.mainFrame = {url:'http://localhost:1234/'}
  const publications = []
  window.webContents.send = (...args) => publications.push(args)
  window.isDestroyed = () => false
  window.getContentSize = () => [1200, 800]
  class FailingView {constructor() {throw Error('Original tab creation failure 原文')}}
  const runtime = createBrowserRuntime({WebContentsView:FailingView, ipcMain, session:{fromPartition:() => browserSession}, getWindow:() => window, getDownloads:() => undefined, getOrigin:() => 'http://localhost:1234'})
  runtime.attach()
  const action = (name, args) => handlers.get('browser')({sender:window.webContents, senderFrame:window.webContents.mainFrame}, name, args)
  await action('bind', {session_id:'first'})
  assert.deepEqual(await action('dialog_response', {session_id:'first',id:'stale',accept:true}),
    {failure:{code:'browser/dialog-inactive',copy:{key:'browser.desktop.dialogInactive',params:{}}}})
  await action('control', {owner:'user'})
  assert.equal((await runtime.bridgeAction({action:'state', session_id:'first', measure:false})).owner, 'user')
  assert.equal((await runtime.bridgeAction({action:'state', session_id:'second', measure:false})).owner, 'agent')
  assert.deepEqual(await action('control', {session_id:'second', owner:'agent'}), {failure:{code:'browser/session-changed',copy:{key:'browser.desktop.sessionChanged',params:{}}}})
  assert.equal((await runtime.bridgeAction({action:'state', session_id:'first', measure:false})).owner, 'user')
  await assert.rejects(handlers.get('browser')({sender:{}, senderFrame:window.webContents.mainFrame}, 'state'), error => error.code === 'browser/request-untrusted')
  await assert.rejects(runtime.bridgeAction({action:'state', session_id:'../bad', measure:false}), error => error.code === 'browser/session-invalid')
  assert.equal(browserSession.check(null, 'notifications', '', {}), false)
  let allowed
  browserSession.request(null, 'notifications', value => {allowed = value}, {})
  assert.equal(allowed, false)
  await action('bind', {session_id:'first'})
  await action('layout', {session_id:'first',visible:true,bounds:{x:0,y:0,width:500,height:300}})
  await action('keyboard_context', {session_id:'first',active:true})
  let prevented=0
  window.webContents.emit('before-input-event',{preventDefault:()=>prevented++},{type:'keyDown',key:'t',meta:process.platform==='darwin',control:process.platform!=='darwin'})
  await action('bind', {session_id:'second'})
  await new Promise(setImmediate)
  assert.equal(prevented,1)
  assert.deepEqual(publications.filter(([kind])=>kind==='browser-error'),[
    ['browser-error',{session_id:'first',message:'Original tab creation failure 原文'}],
  ])
  runtime.suspend()
  runtime.resume()
  runtime.close()
  assert.ok(publications.length > 0)
})

test('bridge tab creation, selection and closing stay isolated with an unsupported dialog adapter', async t => {
  const warnings = t.mock.method(console, 'warn', () => {})
  let next = 0
  const views = []
  class View {
    constructor() {
      this.webContents = new EventEmitter()
      const wc = this.webContents
      wc.id = ++next
      let destroyed = false
      wc.isDestroyed = () => destroyed
      wc.getURL = () => 'about:blank'
      wc.getTitle = () => ''
      wc.isLoading = () => false
      wc.getZoomFactor = () => 1
      wc.navigationHistory = {canGoBack:() => false, canGoForward:() => false}
      wc.setWindowOpenHandler = () => {}
      wc.on('-run-dialog', () => {})
      if (wc.id === 2) wc.on('-run-dialog', () => {})
      wc.debugger = {attach() {}, detach() {}, isAttached:() => true, sendCommand:async () => ({targetInfo:{targetId:`target-${wc.id}`}})}
      wc.loadURL = async () => {}
      wc.focus = () => {}
      wc.close = () => {
        if (wc.blockUnload) {
          let proceed = false
          wc.emit('will-prevent-unload', {preventDefault:() => {proceed = true}})
          if (!proceed) return
        }
        destroyed = true; wc.emit('destroyed')
      }
      views.push(this)
    }
    getBounds() {return {x:0, y:0, width:1024, height:768}}
    setBounds() {}
    setVisible() {}
  }
  const window = {isDestroyed:() => false, getContentSize:() => [1200, 800], contentView:{addChildView() {}, removeChildView() {}}, webContents:{send() {}, focus() {}}}
  const runtime = createBrowserRuntime({WebContentsView:View, getWindow:() => window, getDownloads:() => undefined})
  const first = await runtime.bridgeAction({action:'new_tab', session_id:'first'})
  const second = await runtime.bridgeAction({action:'new_tab', session_id:'second'})
  assert.notEqual(first.target_id, second.target_id)
  assert.equal(warnings.mock.callCount(), 1)
  assert.equal(views[1].webContents.listenerCount('-run-dialog'), 2)
  const snapshot = session_id => runtime.bridgeAction({action:'state', session_id, measure:false})
  assert.equal((await snapshot('first')).active_target, first.target_id)
  assert.equal((await snapshot('second')).tabs.length, 1)
  await assert.rejects(runtime.bridgeAction({action:'select_target', session_id:'first', target_id:second.target_id}), error => error.code === 'browser/target-missing')
  views[0].webContents.blockUnload = true
  views[0].requestClose()
  let dialog = (await snapshot('first')).dialogs[0]
  assert.equal(dialog.message, '')
  assert.deepEqual(dialog.message_copy, {key:'browser.desktop.confirmClose',params:{}})
  assert.equal((await snapshot('second')).dialogs.length, 0)
  await assert.rejects(runtime.bridgeAction({action:'dialog_response',session_id:'second',id:dialog.id,accept:true}), error => error.code === 'browser/dialog-inactive')
  await runtime.bridgeAction({action:'dialog_response',session_id:'first',id:dialog.id,accept:false})
  assert.equal(views[0].webContents.isDestroyed(), false)
  let navigations = 0
  await views[0].navigate(async () => {
    navigations++
    views[0].webContents.emit('will-prevent-unload', {preventDefault() {}})
  })
  dialog = (await snapshot('first')).dialogs[0]
  assert.deepEqual(dialog.message_copy, {key:'browser.desktop.confirmLeave',params:{}})
  assert.equal(navigations, 1)
  await runtime.bridgeAction({action:'dialog_response',session_id:'first',id:dialog.id,accept:true})
  assert.equal(navigations, 2)
  assert.equal((await snapshot('first')).dialogs.length, 0)
  views[0].requestClose()
  dialog = (await snapshot('first')).dialogs[0]
  await runtime.bridgeAction({action:'dialog_response',session_id:'first',id:dialog.id,accept:true})
  assert.equal(views[0].webContents.isDestroyed(), true)
  await runtime.bridgeAction({action:'close_session', session_id:'first'})
  assert.equal(views[0].webContents.isDestroyed(), true)
  assert.equal(views[1].webContents.isDestroyed(), false)
  assert.deepEqual((await snapshot('first')).tabs, [])
  assert.equal((await snapshot('second')).active_target, second.target_id)
  runtime.close()
  assert.equal(views[1].webContents.isDestroyed(), true)
})
