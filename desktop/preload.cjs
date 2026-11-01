const {contextBridge, ipcRenderer} = require('electron')
// 由可信宿主标记平台，网页与 Windows 不启用 macOS 窗口装饰。
if (process.platform === 'darwin') {
  window.addEventListener('DOMContentLoaded', () => {
    document.documentElement.dataset.desktopPlatform = 'darwin'
  }, {once: true})
}
contextBridge.exposeInMainWorld('harnessDesktop', {
  notifyTask: payload => ipcRenderer.invoke('task-notification', payload),
  clearTaskNotification: sessionId => ipcRenderer.invoke('task-notification-clear', sessionId),
  onTaskNotificationOpen: listener => {
    const handler = (_event, sessionId) => listener(sessionId)
    ipcRenderer.on('task-notification-open', handler)
    return () => ipcRenderer.removeListener('task-notification-open', handler)
  },
  setMenu: labels => ipcRenderer.invoke('desktop-menu', labels),
  onCommand: listener => {
    const handler = (_event, action) => listener(action)
    ipcRenderer.on('desktop-command', handler)
    return () => ipcRenderer.removeListener('desktop-command', handler)
  },
  screenshot: (action, hideWindow = false) => ipcRenderer.invoke('screenshot', action, hideWindow),
  browser: (action, args = {}) => ipcRenderer.invoke('browser', action, args),
  onServiceState: listener => {
    const handler = (_event, status) => listener(status)
    ipcRenderer.on('desktop-service-state', handler)
    return () => ipcRenderer.removeListener('desktop-service-state', handler)
  },
  onBrowserError: listener => {
    const handler = (_event, failure) => listener(failure)
    ipcRenderer.on('browser-error', handler)
    return () => ipcRenderer.removeListener('browser-error', handler)
  },
  onFocusFind: listener => {
    const handler = () => listener()
    ipcRenderer.on('browser-focus-find', handler)
    return () => ipcRenderer.removeListener('browser-focus-find', handler)
  },
  onFocusAddress: listener => {
    const handler = () => listener()
    ipcRenderer.on('browser-focus-address', handler)
    return () => ipcRenderer.removeListener('browser-focus-address', handler)
  },
  onBrowserState: listener => {
    const handler = (_event, state) => listener(state)
    ipcRenderer.on('browser-state', handler)
    return () => ipcRenderer.removeListener('browser-state', handler)
  },
})
