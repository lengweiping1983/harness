import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {test} from 'node:test'
import {applicationMenu, attachMenuLifecycle, configureMenuDefaults} from '../application-menu.mjs'
import {attachMenuShortcutGuard} from '../shortcuts.mjs'
import {setupCopy} from '../setup-copy.mjs'

test('menu readiness follows main-document replacement, not iframe or same-document loading', () => {
  const window = new EventEmitter(), changes = []
  window.webContents = new EventEmitter()
  attachMenuLifecycle(window, ready => changes.push(ready))
  window.webContents.emit('did-start-loading')
  window.webContents.emit('did-start-navigation', {isMainFrame:false, isSameDocument:false})
  window.webContents.emit('did-start-navigation', {isMainFrame:true, isSameDocument:true})
  assert.deepEqual(changes, [])
  window.webContents.emit('did-start-navigation', {isMainFrame:true, isSameDocument:false})
  window.webContents.emit('render-process-gone')
  assert.deepEqual(changes, [false, false])
  window.emit('closed')
  assert.equal(window.webContents.listenerCount('did-start-navigation'), 0)
  assert.equal(window.webContents.listenerCount('render-process-gone'), 0)
})

test('menu lifecycle releases its listeners after the window has been destroyed', () => {
  const window = new EventEmitter(), contents = new EventEmitter(), changes = []
  let destroyed = false
  Object.defineProperty(window, 'webContents', {get() {
    if (destroyed) throw new TypeError('Object has been destroyed')
    return contents
  }})
  const otherNavigation = () => {}, otherCrash = () => {}
  contents.on('did-start-navigation', otherNavigation)
  contents.on('render-process-gone', otherCrash)
  attachMenuLifecycle(window, ready => changes.push(ready))
  destroyed = true
  assert.doesNotThrow(() => window.emit('closed'))
  assert.deepEqual(contents.listeners('did-start-navigation'), [otherNavigation])
  assert.deepEqual(contents.listeners('render-process-gone'), [otherCrash])
  contents.emit('did-start-navigation', {isMainFrame:true, isSameDocument:false})
  contents.emit('render-process-gone')
  assert.deepEqual(changes, [])
})

test('native actions stay available during reload while page commands recover with their shortcuts', () => {
  for (const platform of ['darwin', 'win32', 'linux']) for (const language of ['en', 'zh-CN', 'en']) {
    const copy = setupCopy(language), text = key => copy('ui.desktop.menu.' + key)
    for (const ready of [true, false, true]) {
      const actions = []
      const items = applicationMenu({platform, ready, text, command:action => actions.push(action)}).flatMap(menu => menu.submenu)
      assert.equal(items.find(item => item.label === text('about')).enabled, true)
      assert.equal(items.find(item => item.label === text('checkUpdates')).enabled, true)
      for (const [key, action, accelerator] of [
        ['newChat', 'new-chat', 'CommandOrControl+Shift+U'],
        ['screenshot', 'screenshot', 'CommandOrControl+Shift+S'],
        ['screenshotHidden', 'screenshot-hidden', 'CommandOrControl+Shift+H'],
      ]) {
        const item = items.find(item => item.label === text(key))
        assert.equal(item.enabled, ready)
        assert.equal(item.accelerator, accelerator)
        if (ready) item.click()
      }
      assert.deepEqual(actions, ready ? ['new-chat', 'screenshot', 'screenshot-hidden'] : [])
    }
  }
})

test('macOS registers a process default to suppress automatic duplicate fullscreen entries', () => {
  const defaults = [], preferences = {registerDefaults:value => defaults.push(value)}
  configureMenuDefaults(preferences, 'win32')
  configureMenuDefaults(preferences, 'linux')
  assert.deepEqual(defaults, [])
  configureMenuDefaults(preferences, 'darwin')
  assert.deepEqual(defaults, [{NSFullScreenMenuItemEverywhere:false}])
})

test('native menu shortcuts ignore composition and repeats, then recover for ordinary keys', () => {
  const contents = new EventEmitter(), ignored = []
  contents.setIgnoreMenuShortcuts = value => ignored.push(value)
  attachMenuShortcutGuard(contents)
  for (const input of [{isComposing:true}, {isAutoRepeat:true}, {isComposing:false, isAutoRepeat:false}]) {
    contents.emit('before-input-event', {}, input)
  }
  assert.deepEqual(ignored, [true, true, false])
  contents.emit('destroyed')
  assert.equal(contents.listenerCount('before-input-event'), 0)
})
