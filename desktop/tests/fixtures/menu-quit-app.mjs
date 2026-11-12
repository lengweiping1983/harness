import assert from 'node:assert/strict'
import {app, BrowserWindow, Menu} from 'electron'
import {applicationMenu, attachMenuLifecycle} from '../../application-menu.mjs'
import {desktopQuit} from '../../quit.mjs'
import {setupCopy} from '../../setup-copy.mjs'

app.setPath('userData', process.argv[2])
console.log('menu-quit-starting')
const fail = error => {console.error(error); app.exit(1)}
process.once('uncaughtException', fail)
process.once('unhandledRejection', fail)

app.whenReady().then(async () => {
  console.log('menu-quit-ready')
  app.setName('Harness')
  const window = new BrowserWindow({show:false})
  const contents = window.webContents
  let closed = 0, cleaned = 0
  const quit = desktopQuit({app, getWindow:() => window, cleanup:async () => {
    assert.equal(closed, 1)
    await Promise.resolve()
    cleaned++
  }, onError:fail})
  quit.attach(window, () => {throw Error('An unchanged page must not ask for confirmation')})
  const navigationListeners = contents.listeners('did-start-navigation')
  const crashListeners = contents.listeners('render-process-gone')
  attachMenuLifecycle(window, () => {})
  window.once('closed', () => {
    closed++
    assert.equal(window.isDestroyed(), true)
    assert.deepEqual(contents.listeners('did-start-navigation'), navigationListeners)
    assert.deepEqual(contents.listeners('render-process-gone'), crashListeners)
    app.quit()
  })
  app.once('will-quit', () => {
    assert.equal(closed, 1)
    assert.equal(cleaned, 1)
    console.log(`menu-quit-complete:${closed}:${cleaned}`)
  })
  await window.loadURL('data:text/html,<title>Harness menu quit fixture</title>')
  console.log('menu-quit-loaded')
  const copy = setupCopy(process.argv[3])
  const menu = Menu.buildFromTemplate(applicationMenu({text:key => copy('ui.desktop.menu.' + key), command:() => {
    throw Error('Quitting must use the native menu role')
  }}))
  Menu.setApplicationMenu(menu)
  const item = menu.items.flatMap(menu => menu.submenu?.items || []).find(item => item.role === 'quit')
  assert.ok(item)
  assert.equal(item.label, copy('ui.desktop.menu.quit'))
  console.log('menu-quit-invoking')
  if (process.platform === 'darwin') Menu.sendActionToFirstResponder('terminate:')
  else item.click()
}).catch(fail)
