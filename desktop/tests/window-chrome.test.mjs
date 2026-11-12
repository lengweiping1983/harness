import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {test} from 'node:test'
import {applicationMenu} from '../application-menu.mjs'
import {windowChrome, popupApplicationMenu, attachWindowChrome} from '../window-chrome.mjs'

test('only Windows uses the overlay; macOS keeps its existing inset and other platforms keep native chrome', () => {
  assert.deepEqual(windowChrome('darwin'), {titleBarStyle:'hiddenInset', trafficLightPosition:{x:16,y:15}})
  assert.deepEqual(windowChrome('linux'), {})
  assert.equal(windowChrome('win32').titleBarStyle, 'hidden')
  assert.equal(windowChrome('win32').titleBarOverlay.height, 32)
})

test('localized top-level menus retain native roles, accelerators, readiness and action ownership', async () => {
  for (const language of ['en','zh-CN','en']) for (const ready of [false,true]) {
    const actions = [], shown = []
    const template = applicationMenu({platform:'win32', text:key=>language+key, ready, command:action=>actions.push(action)})
    const menu = {items:template.map(group=>({submenu:{popup:options=>{shown.push({group,options}); options.callback()}}}))}
    const window = {getContentSize:()=>[800,600], webContents:{getZoomFactor:()=>1.25}}
    for (const key of ['file','edit','view','window','help']) await popupApplicationMenu(menu, window, {key,x:12,y:28})
    assert.deepEqual(shown.map(item=>item.group.label), ['file','edit','view','window','help'].map(key=>language+key))
    assert.deepEqual(shown.map(item=>[item.options.x,item.options.y]), Array(5).fill([15,35]))
    assert.deepEqual(actions, [])
    assert.equal(template[0].submenu[0].accelerator, 'CommandOrControl+Shift+U')
    assert.equal(template[0].submenu[0].enabled, ready)
    assert.equal(template[1].submenu.find(item=>item.label===language+'paste').role, 'paste')
    assert.equal(template[2].submenu.at(-1).role, 'togglefullscreen')
    template[0].submenu[0].click()
    assert.deepEqual(actions, ['new-chat'])
    for (const request of [{key:'bad',x:0,y:0},{key:'edit',x:-1,y:0},{key:'edit',x:800,y:0},{key:'edit',x:1.5,y:0},{key:'edit',x:0,y:600}]) assert.throws(()=>popupApplicationMenu(menu,window,request))
  }
})

test('File omits Close Window and the platform retains exactly one native Quit action', () => {
  for (const platform of ['darwin', 'win32', 'linux']) for (const language of ['en', 'zh-CN', 'en']) {
    const template = applicationMenu({platform, text: key => language + key, command: () => {}})
    const file = template.find(group => group.label === language + 'file').submenu
    const items = template.flatMap(group => group.submenu)
    assert.equal(items.some(item => item.role === 'close'), false)
    assert.equal(items.filter(item => item.role === 'quit').length, 1)
    assert.equal(file.at(-1).type === 'separator', false)
    assert.equal(file.some(item => item.role === 'quit'), platform !== 'darwin')
    if (platform === 'darwin') assert.equal(template[0].submenu.at(-1).role, 'quit')
  }
})

test('chrome IPC admits only the application main frame and releases its handlers when the window closes', async () => {
  const window = Object.assign(new EventEmitter(), {webContents:{mainFrame:{url:'http://127.0.0.1:8765/'}, getZoomFactor:()=>1}, getContentSize:()=>[800,600], setTitleBarOverlay:value=>appearances.push(value)})
  const handlers = new Map(), appearances = [], popups = []
  const ipcMain = {handle:(name,handler)=>handlers.set(name,handler), removeHandler:name=>handlers.delete(name)}
  const Menu = {getApplicationMenu:()=>({items:[{submenu:{popup:options=>{popups.push(options);options.callback()}}}]})}
  for (const platform of ['darwin','linux']) {attachWindowChrome({window,ipcMain,Menu,origin:'http://127.0.0.1:8765',platform});assert.equal(handlers.size,0)}
  attachWindowChrome({window,ipcMain,Menu,origin:'http://127.0.0.1:8765',platform:'win32'})
  const event = {sender:window.webContents, senderFrame:window.webContents.mainFrame}
  const appearance = handlers.get('desktop-titlebar-appearance'), popup = handlers.get('desktop-menu-popup')
  for (const invalid of [{sender:{},senderFrame:event.senderFrame},{sender:event.sender,senderFrame:{url:event.senderFrame.url}}]) {
    assert.throws(()=>appearance(invalid,{color:'#ffffff',symbolColor:'#000000'}), /Untrusted/)
    assert.throws(()=>popup(invalid,{key:'file',x:8,y:28}), /Untrusted/)
  }
  window.webContents.mainFrame.url = 'https://untrusted.invalid/'
  assert.throws(()=>appearance(event,{color:'#ffffff',symbolColor:'#000000'}), /Untrusted/)
  window.webContents.mainFrame.url = 'http://127.0.0.1:8765/'
  for (const value of [null,{}, {color:'red',symbolColor:'#000000'}, {color:'#ffffff',symbolColor:null}]) assert.throws(()=>appearance(event,value))
  for (const value of [{color:'#f0f2f3',symbolColor:'#20292c'},{color:'#1c2123',symbolColor:'#e9eeef'}]) appearance(event,value)
  assert.equal(appearances.length,2)
  await popup(event,{key:'file',x:8,y:28})
  assert.equal(popups.length,1)
  window.emit('closed')
  assert.equal(handlers.size,0)
})
