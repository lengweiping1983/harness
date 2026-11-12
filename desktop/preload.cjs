const {contextBridge, ipcRenderer} = require('electron')
// 由可信宿主标记平台，普通网页不启用桌面窗口装饰。
if (['darwin', 'win32'].includes(process.platform)) {
  window.addEventListener('DOMContentLoaded', () => {
    document.documentElement.dataset.desktopPlatform = process.platform
  }, {once: true})
}
contextBridge.exposeInMainWorld('harnessDesktop', {
  platform: process.platform,
  requestSystemPermission: capability => ipcRenderer.invoke('system-permission', capability),
  voice: {
    context: value => ipcRenderer.invoke('desktop-voice', 'context', value),
    receipt: (id, accepted) => ipcRenderer.invoke('desktop-voice', 'receipt', {id, accepted}),
    onInsert: listener => {
      const handler = (_event, value) => listener(value)
      ipcRenderer.on('voice-insert', handler)
      return () => ipcRenderer.removeListener('voice-insert', handler)
    },
    status: () => ipcRenderer.invoke('desktop-voice', 'status'),
    configure: value => ipcRenderer.invoke('desktop-voice', 'configure', value),
    permission: () => ipcRenderer.invoke('desktop-voice', 'permission'),
    ready: () => ipcRenderer.invoke('voice-capture', 'ready'),
    phase: (id, phase) => ipcRenderer.invoke('voice-capture', 'phase', {id, phase}),
    complete: (id, transcript) => ipcRenderer.invoke('voice-capture', 'complete', {id, transcript}),
    cancel: id => ipcRenderer.invoke('voice-capture', 'cancel', {id}),
    onAction: listener => {
      const handler = (_event, value) => listener(value)
      ipcRenderer.on('voice-action', handler)
      return () => ipcRenderer.removeListener('voice-action', handler)
    },
  },
  popupMenu: request => ipcRenderer.invoke('desktop-menu-popup', request),
  setTitleBarAppearance: value => ipcRenderer.invoke('desktop-titlebar-appearance', value),
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
