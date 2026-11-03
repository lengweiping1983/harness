import {TaskNotifications} from './task-notifications.mjs'
import {BrowserDownloads} from './downloads.mjs'
import {fileURLToPath} from 'node:url'
import path from 'node:path'
import {createBrowserRuntime} from './browser-runtime.mjs'
import {claimDesktopInstance} from './single-instance.mjs'
import {desktopQuit} from './quit.mjs'
import updaterPackage from 'electron-updater'
import {DesktopUpdates} from './updates.mjs'
import {applicationMenu} from './application-menu.mjs'
import {windowChrome, attachWindowChrome} from './window-chrome.mjs'
import {configureAboutPanel} from './about-panel.mjs'
import {autoUpdater as nativeAutoUpdater, app, BrowserWindow, WebContentsView, ipcMain, session, shell, clipboard, dialog, Menu, screen, desktopCapturer, protocol, Notification} from 'electron'
import {ScreenshotSession} from './screenshot.mjs'
import {FastScreenCapture} from './fast-screen-capture.mjs'
import {ScreenshotImages, screenshotScheme} from './screenshot-images.mjs'
import {startBridge} from './bridge.mjs'
import {BackendSupervisor} from './backend-supervisor.mjs'
import {availablePort} from './backend.mjs'

protocol.registerSchemesAsPrivileged([screenshotScheme])
/** 桌面文案占位符替换：{version} 为当前应用版本。 */
function formatDesktopText(text, values = {version: app.getVersion()}) {
  return text.replace(/\{(\w+)\}/g, (original, name) => Object.hasOwn(values, name) ? String(values[name]) : original)
}
async function startDesktop() {
  let window
  const instanceReady = claimDesktopInstance(app, () => window)
  if (!instanceReady) return
  const debugPort = await availablePort()
  app.commandLine.appendSwitch('remote-debugging-port', String(debugPort))
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1')
  let applicationURL
  let origin
  let backend
  let bridge
  let fastCapture
  const quit = desktopQuit({app, getWindow: () => window,
    cleanup: async () => {
      downloads?.close()
      try {await backend?.close()}
      finally {
        try {await bridge?.close()}
        finally {fastCapture?.close()}
      }
    }, onError: error => console.error('Desktop cleanup failed:', error)})
  let downloads
  const browser = createBrowserRuntime({app, WebContentsView, ipcMain, session, shell, Menu, clipboard, getWindow:() => window, getDownloads:() => downloads, getBackend:() => backend, getOrigin:() => origin, getMenuText:() => menuText})
  await app.whenReady()
  const brandIcon = fileURLToPath(new URL(process.platform === 'darwin' ? './assets/harness-macos.png' : './assets/harness.png', import.meta.url))
  if (process.platform === 'darwin') app.dock?.setIcon(brandIcon)
  fastCapture = ['win32','darwin'].includes(process.platform) ? new FastScreenCapture() : null
  app.on('will-quit', () => fastCapture?.close())
  bridge = await startBridge(browser.bridgeAction)
  try {
    backend = new BackendSupervisor({root: app.isPackaged ? app.getPath('home') : fileURLToPath(new URL('../', import.meta.url)), resources:app.isPackaged ? process.resourcesPath : undefined, bridge, cdp: `http://127.0.0.1:${debugPort}`}, status => {if (window && !window.isDestroyed()) window.webContents.send('desktop-service-state', status)})
    applicationURL = (await backend.start()).url
    origin = new URL(applicationURL).origin
  } catch (error) {dialog.showErrorBox('Harness startup failed', String(error)); app.quit(); throw error}
  // The application window uses the default Electron session, while website
  // permissions are scoped to the separate browser session. Explicitly grant
  // microphone access only to the local Harness origin so Chromium does not
  // reject getUserMedia on Windows before the renderer can start recording.
  const appSession = session.defaultSession
  const allowsApplicationAudio = (webContents, permission, requestingOrigin, details = {}) => {
    let requestOrigin = requestingOrigin
    try {requestOrigin = new URL(requestingOrigin).origin} catch {}
    if (webContents !== window?.webContents || permission !== 'media' || requestOrigin !== origin) return false
    const mediaTypes = details.mediaTypes || (details.mediaType ? [details.mediaType] : [])
    return mediaTypes.length === 1 && mediaTypes[0] === 'audio'
  }
  appSession.setPermissionCheckHandler((webContents, permission, requestingOrigin, details) =>
    allowsApplicationAudio(webContents, permission, requestingOrigin, details))
  appSession.setPermissionRequestHandler((webContents, permission, callback, details) =>
    callback(allowsApplicationAudio(webContents, permission, details?.requestingUrl || '', details)))
  downloads = new BrowserDownloads(() => path.join(app.getPath('downloads'), 'Harness'), () => browser.publish())
  window = new BrowserWindow({width: 1440, height: 960, minWidth: 800, minHeight: 600, title: 'Harness', icon: brandIcon, ...windowChrome(), webPreferences: {preload: fileURLToPath(new URL('./preload.cjs', import.meta.url)), nodeIntegration: false, contextIsolation: true, sandbox: true}})
  instanceReady()
  let menuReady = false
  const updates = new DesktopUpdates({updater:updaterPackage.autoUpdater, nativeUpdater:process.platform === 'darwin' ? nativeAutoUpdater : null, packaged:app.isPackaged, notify:() => installMenu(menuReady)})
  let updateDialog = false
  async function showUpdates() {
    if (updateDialog) return
    updateDialog = true
    const message = async key => dialog.showMessageBox(window, {type:'info', title:'Harness', message:formatDesktopText(menuText[key]), buttons:[menuText.ok]})
    async function showDownloaded() {
      if (window.isDestroyed()) return
      const choice = await dialog.showMessageBox(window, {type:'info', title:'Harness', message:menuText.updateDownloaded, detail:updates.version || '', buttons:[menuText.quitInstall, menuText.later], defaultId:1, cancelId:1})
      if (choice.response === 0) app.quit()
    }
    try {
      const result = await updates.check()
      if (window.isDestroyed()) return
      if (result.state === 'available') {
        const choice = await dialog.showMessageBox(window, {type:'info', title:'Harness', message:menuText.updateAvailable, detail:result.version, buttons:[menuText.downloadUpdate, menuText.later], defaultId:0, cancelId:1})
        if (choice.response === 0) {await updates.download(); await showDownloaded()}
      } else if (result.state === 'downloaded') await showDownloaded()
      else await message(result.state === 'development' ? 'updateDevelopment' : 'updateCurrent')
    } catch (error) {console.error('Update failed:', error.message); if (!window.isDestroyed()) await message('updateFailed')}
    finally {updateDialog = false}
  }
  let menuText = null
  quit.attach(window, () => dialog.showMessageBoxSync(window, {
    type: 'question', title: 'Harness',
    message: menuText?.unsavedChanges ?? 'Unsaved changes will be lost if you leave.',
    buttons: [menuText?.keepEditing ?? 'Keep editing', menuText?.discardLeave ?? 'Discard and leave'],
    defaultId: 0, cancelId: 0,
  }) === 1)
  function installMenu(ready) {
    menuReady = ready
    if (!menuText || window.isDestroyed()) return
    Menu.setApplicationMenu(Menu.buildFromTemplate(applicationMenu({text:key => menuText[key], ready, updateState:updates.state, updateProgress:updates.progress, command:action => {
      if (action === 'updates') {void showUpdates(); return}
      if (action === 'about') {app.showAboutPanel(); return}
      window.webContents.send('desktop-command', action)
    }})))
  }
  configureAboutPanel(app, {iconPath: brandIcon})
  attachWindowChrome({window, ipcMain, Menu, origin})
  ipcMain.handle('desktop-menu', (event, labels) => {
    if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || new URL(event.senderFrame.url).origin !== origin) throw Error('Untrusted menu request')
    const keys = ['about','settings','hide','hideOthers','unhide','quit','file','newChat','edit','undo','redo','cut','copy','paste','selectAll','view','commands','sidebar','inspector','files','terminal','browser','fullscreen','window','minimize','zoom','front','help','shortcuts','checkUpdates','updateDevelopment','updateCurrent','updateAvailable','updateDownloaded','updateFailed','downloadUpdate','later','ok','updateAvailableMenu','openProject','updateCheckingMenu','updateDownloadingMenu','updateReadyMenu','unsavedChanges','keepEditing','discardLeave','contextOpenLink','contextCopyLink','contextInspect','quitInstall']
    if (!labels || keys.some(key => typeof labels[key] !== 'string' || !labels[key].trim() || labels[key].length > 120)) throw Error('Invalid menu labels')
    menuText = Object.fromEntries(keys.map(key => [key, labels[key]]))
    installMenu(true)
  })
  const taskNotifications = new TaskNotifications({Notification, getWindow: () => window})
  const trustedNotification = event => event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame && new URL(event.senderFrame.url).origin === origin
  ipcMain.handle('task-notification', (event, payload) => {
    if (!trustedNotification(event)) throw Error('Untrusted notification request')
    return taskNotifications.show(payload)
  })
  ipcMain.handle('task-notification-clear', (event, sessionId) => {
    if (!trustedNotification(event) || typeof sessionId !== 'string') throw Error('Untrusted notification request')
    taskNotifications.clear(sessionId)
  })
  window.on('closed', () => taskNotifications.close())
  window.webContents.on('did-start-loading', () => installMenu(false))
  window.webContents.setWindowOpenHandler(() => ({action: 'deny'}))
  window.webContents.on('will-navigate', (event, url) => {if (new URL(url).origin !== origin) event.preventDefault()})
  const screenshotImages=new ScreenshotImages(origin)
  protocol.handle('harness-screenshot', request=>screenshotImages.response(request))
  const screenshot = new ScreenshotSession({window, screen, desktopCapturer,
    publishImage:(bytes,type)=>screenshotImages.publish(bytes,type),releaseImage:()=>screenshotImages.clear(),
    captureScreen:fastCapture ? (display, screen) => fastCapture.capture(display, screen) : undefined,
    suspend: () => browser.suspend(),
    resume: () => browser.resume()})
  ipcMain.handle('screenshot', async (event, action, hideWindow) => {
    if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || new URL(event.senderFrame.url).origin !== origin) throw Error('Untrusted screenshot request')
    if (action === 'capture') return screenshot.capture(hideWindow)
    if (action === 'finish') {screenshot.finish(); return null}
    throw Error('Unknown screenshot action')
  })
  window.webContents.on('render-process-gone', () => screenshot.finish())
  window.webContents.on('did-start-navigation', (_event, _url, inPlace, mainFrame) => {if (mainFrame && !inPlace) screenshot.finish()})
  browser.attach()
  window.on('closed', () => { browser.close(); app.quit() })
  await fastCapture?.ready?.catch(() => {})
  await window.loadURL(applicationURL)
  if (app.isPackaged) void updates.check().catch(error => console.error('Update check failed:', error.message))

}
void startDesktop().catch(error => {console.error(error); app.quit()})
