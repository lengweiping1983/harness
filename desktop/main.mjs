import {TaskNotifications} from './task-notifications.mjs'
import {BrowserDownloads} from './downloads.mjs'
import {fileURLToPath} from 'node:url'
import path from 'node:path'
import {createBrowserRuntime} from './browser-runtime.mjs'
import {claimDesktopInstance} from './single-instance.mjs'
import {desktopQuit} from './quit.mjs'
import updaterPackage from 'electron-updater'
import {DesktopUpdates} from './updates.mjs'
import {InstallerUpdates} from './installer-updates.mjs'
import {MacInstaller} from './mac-installer.mjs'
import {createUpdateFetch, updateNetworkKind, networkSettingsURL} from './update-network.mjs'
import {updateDialogOptions, updateFailureOptions} from './update-dialogs.mjs'
import {readFileSync} from 'node:fs'
import {DesktopVoice, voiceReply} from './voice.mjs'
import {attachApplicationPermissions} from './application-permissions.mjs'
import {applicationMenu, refreshUpdateMenu, updateDockProgress, attachMenuLifecycle, configureMenuDefaults} from './application-menu.mjs'
import {attachMenuShortcutGuard} from './shortcuts.mjs'
import {windowChrome, attachWindowChrome} from './window-chrome.mjs'
import {configureAboutPanel} from './about-panel.mjs'
import {autoUpdater as nativeAutoUpdater, app, BrowserWindow, WebContentsView, ipcMain, session, shell, clipboard, dialog, Menu, screen, desktopCapturer, protocol, Notification, systemPreferences, globalShortcut} from 'electron'
import {ScreenshotSession} from './screenshot.mjs'
import {FastScreenCapture} from './fast-screen-capture.mjs'
import {SystemPermissions, screenshotReply} from './system-permissions.mjs'
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
  configureMenuDefaults(systemPreferences)
  const debugPort = await availablePort()
  app.commandLine.appendSwitch('remote-debugging-port', String(debugPort))
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1')
  let applicationURL
  let origin
  let backend
  let bridge
  let fastCapture
  let voice
  let updates
  let installRequested = false
  let menuText = null
  const systemPermissions = new SystemPermissions({systemPreferences, desktopCapturer, shell})
  const quit = desktopQuit({app, getWindow: () => window,
    cleanup: async () => {
      const failures = []
      for (const close of [() => updates?.close?.({install:installRequested}), () => downloads?.close(),
        () => voice?.close(), () => backend?.close(), () => bridge?.close(), () => fastCapture?.close()]) {
        try {await close()} catch (error) {failures.push(error)}
      }
      if (failures.length === 1) throw failures[0]
      if (failures.length) throw new AggregateError(failures, failures.map(error => error.message).join('\n'))
    }, beforeQuit:async ({cleanupFailed}) => {
      if (installRequested && cleanupFailed) throw Error(menuText.updateInstallFailed)
      if (installRequested) await updates.installer.install(menuText.updateInstallFailed)
    }, onError: error => {
      console.error('Desktop cleanup failed:', error)
      if (installRequested) {
        installRequested = false
        dialog.showErrorBox('Harness', `${menuText.updateInstallFailed}\n${error.message}`)
      }
    }})
  let downloads
  const browser = createBrowserRuntime({app, WebContentsView, ipcMain, session, shell, Menu, clipboard, systemPermissions, getWindow:() => window, getDownloads:() => downloads, getBackend:() => backend, getOrigin:() => origin, getMenuText:() => menuText})
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
  // 应用主窗口的剪贴板写入与录音权限不影响第三方网页的独立权限会话。
  attachApplicationPermissions(session.defaultSession, () => ({origin, window, voiceWindow:voice?.window}))
  downloads = new BrowserDownloads(() => path.join(app.getPath('downloads'), 'Harness'), () => browser.publish())
  window = new BrowserWindow({width: 1440, height: 960, minWidth: 800, minHeight: 600, title: 'Harness', icon: brandIcon, ...windowChrome(), webPreferences: {preload: fileURLToPath(new URL('./preload.cjs', import.meta.url)), nodeIntegration: false, contextIsolation: true, sandbox: true}})
  instanceReady()
  voice = new DesktopVoice({app, BrowserWindow, globalShortcut, clipboard, screen, systemPreferences, shell, systemPermissions,
    getWindow:() => window, getURL:() => applicationURL, preload:fileURLToPath(new URL('./preload.cjs', import.meta.url)),
    nativePath:app.isPackaged ? path.join(process.resourcesPath, 'runtime/voice', process.platform === 'win32' ? 'voice-native.ps1' : 'voice-native')
      : fileURLToPath(new URL(process.platform === 'win32' ? './voice-native.ps1' : './voice-native.swift', import.meta.url))})
  ipcMain.handle('desktop-voice', (event, action, value) => voiceReply(async () => {
    if (!voice.trusted(event, window)) throw Error('Untrusted voice settings request')
    if (action === 'status') return voice.snapshot()
    if (action === 'context') {voice.setContext(value); return null}
    if (action === 'receipt') {voice.receive(value); return null}
    if (action === 'configure') return voice.configure(value)
    if (action === 'permission') return voice.requestPermission()
    throw Error('Unknown voice settings action')
  }))
  ipcMain.handle('voice-capture', (event, action, value = {}) => voiceReply(async () => {
    if (!voice.trusted(event, voice.window)) throw Error('Untrusted voice capture request')
    if (action === 'ready') return voice.ready()
    if (!value || typeof value !== 'object' || Array.isArray(value) || value.id !== undefined && typeof value.id !== 'string') throw Error('Invalid voice capture request')
    if (action === 'phase') {voice.phase(value.id, value.phase); return null}
    if (action === 'complete') return voice.complete(value.id, value.transcript)
    if (action === 'cancel') {voice.cancel(value.id); return null}
    throw Error('Unknown voice capture action')
  }))
  await voice.initialize()
  ipcMain.handle('system-permission', (event, capability) => voiceReply(async () => {
    if (!voice.trusted(event, window) && !voice.trusted(event, voice.window)) throw Error('Untrusted system permission request')
    if (capability !== 'microphone') throw Error('Invalid system permission request')
    return systemPermissions.request(capability)
  }))
  let menuReady = false
  const metadata = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))
  const updateFetch = createUpdateFetch(session.fromPartition('harness-updates', {cache:false}))
  updates = metadata.harnessUpdateSource?.provider === 'github'
    ? new InstallerUpdates({source:metadata.harnessUpdateSource, version:app.getVersion(), packaged:app.isPackaged, fetch:updateFetch, installer:process.platform === 'darwin' ? new MacInstaller({fetch:updateFetch, appPath:path.resolve(path.dirname(app.getPath('exe')), '../..'), cachePath:path.join(app.getPath('home'), 'Library', 'Caches', 'Harness', 'updates')}) : null, notify:refreshUpdates})
    : new DesktopUpdates({updater:updaterPackage.autoUpdater, nativeUpdater:process.platform === 'darwin' ? nativeAutoUpdater : null, packaged:app.isPackaged, notify:refreshUpdates})
  let updateDialog = false
  async function showUpdates() {
    if (updateDialog) return
    updateDialog = true
    let retryRequested = false
    const message = async key => dialog.showMessageBox(window, {type:'info', title:'Harness', message:formatDesktopText(menuText[key]), buttons:[menuText.ok]})
    async function showDownloaded() {
      if (window.isDestroyed()) return
      const choice = await dialog.showMessageBox(window, updateDialogOptions(updates.snapshot(), Boolean(updates.installer), menuText))
      if (choice.response === 0) {installRequested = Boolean(updates.installer); app.quit()}
    }
    try {
      const result = await updates.check()
      if (window.isDestroyed()) return
      if (result.state === 'available') {
        const choice = await dialog.showMessageBox(window, updateDialogOptions(result, Boolean(updates.installer), menuText))
        if (choice.response === 0) {
          if (result.mode === 'installer' && !updates.installer) await shell.openExternal(result.downloadUrl)
          else {await updates.download(); await showDownloaded()}
        }
      } else if (result.state === 'downloaded') await showDownloaded()
      else if (result.state === 'unavailable') await message(result.reason === 'platform' ? 'updatePlatformUnavailable' : 'updateUnpublished')
      else await message(result.state === 'development' ? 'updateDevelopment' : 'updateCurrent')
    } catch (error) {
      console.error('Update failed:', error.message)
      if (!window.isDestroyed()) {
        const repository = metadata.harnessUpdateSource
        const settingsURL = networkSettingsURL()
        const failure = updateFailureOptions(error, menuText, {repository, networkSettings:Boolean(settingsURL), networkKind:updateNetworkKind(error)})
        const choice = await dialog.showMessageBox(window, failure.options)
        const action = failure.actions[choice.response]
        if (action === 'retry') retryRequested = true
        else if (action === 'networkSettings' || action === 'releases') {
          const destination = action === 'networkSettings' ? settingsURL : `https://github.com/${repository.owner}/${repository.repo}/releases`
          try {await shell.openExternal(destination)}
          catch (openError) {
            console.error('Update destination failed:', openError)
            if (!window.isDestroyed()) await dialog.showMessageBox(window, {type:'warning', title:'Harness', message:menuText.updateOpenFailed, detail:openError.message, buttons:[menuText.ok]})
          }
        }
      }
    }
    finally {updateDialog = false}
    if (retryRequested && !window.isDestroyed()) return showUpdates()
  }
  quit.attach(window, () => {
    const leave = dialog.showMessageBoxSync(window, {
      type: 'question', title: 'Harness',
      message: menuText?.unsavedChanges ?? 'Unsaved changes will be lost if you leave.',
      buttons: [menuText?.keepEditing ?? 'Keep editing', menuText?.discardLeave ?? 'Discard and leave'],
      defaultId: 0, cancelId: 0,
    }) === 1
    if (!leave) installRequested = false
    return leave
  })
  function refreshUpdates() {
    if (window.isDestroyed()) return
    const snapshot = updates.snapshot()
    window.setProgressBar(updateDockProgress(snapshot))
    if (menuText) refreshUpdateMenu(Menu.getApplicationMenu(), snapshot, key => menuText[key])
  }
  function installMenu(ready) {
    menuReady = ready
    if (!menuText || window.isDestroyed()) return
    Menu.setApplicationMenu(Menu.buildFromTemplate(applicationMenu({text:key => menuText[key], ready, updateState:updates.state, updateProgress:updates.progress, updatePhase:updates.phase, updateReceived:updates.received, updateTotal:updates.total, command:action => {
      if (action === 'updates') {void showUpdates(); return}
      if (action === 'about') {app.showAboutPanel(); return}
      window.webContents.send('desktop-command', action)
    }})))
  }
  configureAboutPanel(app, {iconPath: brandIcon})
  attachWindowChrome({window, ipcMain, Menu, origin})
  ipcMain.handle('desktop-menu', (event, labels) => {
    if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || new URL(event.senderFrame.url).origin !== origin) throw Error('Untrusted menu request')
    const keys = ['about','settings','hide','hideOthers','unhide','quit','file','newChat','screenshot','screenshotHidden','edit','undo','redo','cut','copy','paste','selectAll','view','commands','sidebar','inspector','files','terminal','browser','fullscreen','window','minimize','zoom','front','help','shortcuts','checkUpdates','updateDevelopment','updateCurrent','updateAvailable','updateDownloaded','updateFailed','updateInstallerDetail','updateMacInstallerDetail','updateMacDownloaded','updateInstallFailed','updateIntegrityFailed','updateInstallLocation','updateCurrentSignatureInvalid','updateNetworkDetail','updateNetworkDns','updateNetworkProxy','updateNetworkTls','updateNetworkOffline','updateNetworkTimeout','updateNetworkConnection','updateOpenFailed','updateRetry','updateNetworkSettings','updateUnpublished','updatePlatformUnavailable','updateSourceUnavailable','updateInvalidRelease','updateMetadataTooLarge','updateHttpFailure','openReleasePage','downloadUpdate','later','ok','updateAvailableMenu','openProject','updateCheckingMenu','updateDownloadingMenu','updateConnectingMenu','updateVerifyingMenu','updatePreparingMenu','updateTransferMenu','updateReadyMenu','unsavedChanges','keepEditing','discardLeave','contextOpenLink','contextCopyLink','contextInspect','quitInstall']
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
  attachMenuLifecycle(window, installMenu)
  attachMenuShortcutGuard(window.webContents)
  window.webContents.setWindowOpenHandler(() => ({action: 'deny'}))
  window.webContents.on('will-navigate', (event, url) => {if (new URL(url).origin !== origin) event.preventDefault()})
  const screenshotImages=new ScreenshotImages(origin)
  protocol.handle('harness-screenshot', request=>screenshotImages.response(request))
  const screenshot = new ScreenshotSession({window, screen, desktopCapturer,
    authorize:() => systemPermissions.request('screen'),
    publishImage:(bytes,type)=>screenshotImages.publish(bytes,type),releaseImage:()=>screenshotImages.clear(),
    captureScreen:fastCapture ? (display, screen) => fastCapture.capture(display, screen) : undefined,
    suspend: () => browser.suspend(),
    resume: () => browser.resume()})
  ipcMain.handle('screenshot', async (event, action, hideWindow) => {
    if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || new URL(event.senderFrame.url).origin !== origin) throw Error('Untrusted screenshot request')
    if (action === 'capture') return screenshotReply(() => screenshot.capture(hideWindow), action)
    if (action === 'finish') return screenshotReply(() => {screenshot.finish(); return null}, action)
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
