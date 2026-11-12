export function windowChrome(platform = process.platform) {
  if (platform === 'darwin') return {titleBarStyle: 'hiddenInset', trafficLightPosition: {x: 16, y: 15}}
  if (platform === 'win32') return {titleBarStyle: 'hidden', titleBarOverlay: {color: '#f0f2f3', symbolColor: '#20292c', height: 32}}
  return {}
}

export function titleBarAppearance(value) {
  if (!value || typeof value.color !== 'string' || typeof value.symbolColor !== 'string' || !/^#[\da-f]{6}$/i.test(value.color) || !/^#[\da-f]{6}$/i.test(value.symbolColor)) throw Error('Invalid title bar appearance')
  return {color: value.color, symbolColor: value.symbolColor}
}

export function popupApplicationMenu(menu, window, request) {
  const keys = ['file', 'edit', 'view', 'window', 'help']
  const index = keys.indexOf(request?.key)
  if (index < 0 || !Number.isSafeInteger(request.x) || !Number.isSafeInteger(request.y)) throw Error('Invalid menu position')
  const [width, height] = window.getContentSize()
  const zoom = window.webContents.getZoomFactor()
  const x = Math.round(request.x * zoom), y = Math.round(request.y * zoom)
  if (x < 0 || x >= width || y < 0 || y >= height) throw Error('Invalid menu position')
  const submenu = menu?.items[index]?.submenu
  if (!submenu) throw Error('Application menu is not ready')
  return new Promise(resolve => submenu.popup({window, x, y, callback: resolve}))
}

export function attachWindowChrome({window, ipcMain, Menu, origin, platform = process.platform}) {
  if (platform !== 'win32') return
  const trusted = event => event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame && new URL(event.senderFrame.url).origin === origin
  ipcMain.handle('desktop-titlebar-appearance', (event, value) => {
    if (!trusted(event)) throw Error('Untrusted title bar request')
    window.setTitleBarOverlay(titleBarAppearance(value))
  })
  ipcMain.handle('desktop-menu-popup', (event, request) => {
    if (!trusted(event)) throw Error('Untrusted menu request')
    return popupApplicationMenu(Menu.getApplicationMenu(), window, request)
  })
  window.once('closed', () => {
    ipcMain.removeHandler('desktop-titlebar-appearance')
    ipcMain.removeHandler('desktop-menu-popup')
  })
}
