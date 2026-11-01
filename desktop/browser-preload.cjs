const {contextBridge, ipcRenderer} = require('electron')
// Electron disables prompt(). Expose only its synchronous browser contract;
// the desktop host validates the sender and owns the answer UI.
contextBridge.exposeInMainWorld('__harnessWebsitePrompt', (message = '', defaultValue = '') =>
  ipcRenderer.sendSync('website-prompt', String(message).slice(0, 4000), String(defaultValue).slice(0, 2000)))
contextBridge.executeInMainWorld({func: () => {window.prompt = window.__harnessWebsitePrompt}})
// 原生视图的 resize 可能晚于宿主几何回包；让实际渲染变化撤销旧视口缓存。
window.addEventListener('resize', () => ipcRenderer.send('website-viewport-changed'))
