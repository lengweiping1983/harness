import {app, BrowserWindow, Menu, ipcMain} from 'electron'
import {fileURLToPath} from 'node:url'
import {windowChrome, attachWindowChrome} from '../../window-chrome.mjs'
import {applicationMenu} from '../../application-menu.mjs'

app.setPath('userData', process.argv.at(-2))
app.whenReady().then(async () => {
  const url = process.argv.at(-1)
  const window = new BrowserWindow({width:1440,height:900,minWidth:800,minHeight:600,title:'Harness Windows titlebar acceptance',...windowChrome(),
    webPreferences:{preload:fileURLToPath(new URL('../../preload.cjs',import.meta.url)),contextIsolation:true,sandbox:true,nodeIntegration:false}})
  attachWindowChrome({window,ipcMain,Menu,origin:new URL(url).origin})
  ipcMain.handle('desktop-menu', (_event, labels) => {
    Menu.setApplicationMenu(Menu.buildFromTemplate(applicationMenu({text:key=>labels[key],command:action=>window.webContents.send('desktop-command',action)})))
  })
  ipcMain.handle('browser', (_event, action) => {
    if (action === 'service_state') return {status:'running'}
    throw Error('Native browsing is outside this layout fixture')
  })
  window.on('closed',()=>app.quit())
  await window.loadURL(url)
  window.show()
}).catch(error=>{console.error(error);app.exit(1)})
