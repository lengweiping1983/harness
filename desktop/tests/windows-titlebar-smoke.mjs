import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {mkdtemp, mkdir, rm, writeFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {createRequire} from 'node:module'
import {fileURLToPath} from 'node:url'
import {stopChild} from '../stop-child.mjs'

const requireWeb = createRequire(new URL('../../web/package.json',import.meta.url))
const {_electron} = requireWeb('playwright-core')
const {createServer} = await import(new URL('../../web/node_modules/vite/dist/node/index.js',import.meta.url).href)
assert.equal(process.platform,'win32','Native Windows title bar smoke requires Windows')
const root = fileURLToPath(new URL('../../',import.meta.url))
const profile = await mkdtemp(path.join(tmpdir(),'harness-titlebar-'))
const screenshots = path.join(root,'output/windows-titlebar')
await mkdir(screenshots,{recursive:true})
const python = process.env.HARNESS_PYTHON || path.join(root,'.venv/Scripts/python.exe')
const backend = spawn(python,['-X','utf8','-m','tests.web_fixture'],{cwd:root,stdio:['ignore','pipe','pipe']})
let diagnostics='', app, server, timer, page
backend.stderr.on('data',data=>{diagnostics+=data})
const lines=createInterface({input:backend.stdout})
const failures=[]
try {
  const port = await new Promise((resolve,reject)=>{
    timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+diagnostics)),30000)
    backend.once('error',reject)
    backend.once('exit',code=>reject(Error('Fixture exited: '+code+' '+diagnostics)))
    lines.on('line',line=>{try{const value=JSON.parse(line);if(Number.isInteger(value.port))resolve(value.port)}catch{}})
  })
  clearTimeout(timer)
  process.chdir(path.join(root,'web'))
  server=await createServer({root:path.join(root,'web'),cacheDir:path.join(profile,'vite-cache'),server:{port:0,host:'127.0.0.1',proxy:{'/api/v1':{target:'http://127.0.0.1:'+port,changeOrigin:false},'/ws':{target:'http://127.0.0.1:'+port,ws:true,changeOrigin:false}}}})
  await server.listen()
  const url=server.resolvedUrls.local[0]
  console.log('Isolated backend and Vite ready')
  const env={...process.env};delete env.ELECTRON_RUN_AS_NODE
  app=await _electron.launch({executablePath:createRequire(new URL('../package.json',import.meta.url))('electron'),args:[fileURLToPath(new URL('./fixtures/windows-titlebar-app.mjs',import.meta.url)),profile,url],env,timeout:30000})
  page=await app.firstWindow();page.setDefaultTimeout(10000)
  const errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.locator('.app-shell').waitFor({timeout:60000})
  console.log('Native application booted')
  await page.waitForFunction(()=>document.querySelector('.windows-titlebar button:not(:disabled)'))
  assert.equal(await page.evaluate(async()=>{const {useAppStore}=await import('/src/store/app.ts');return useAppStore.getState().sessions.length}),0,'Fixture must not read existing user conversations')
  const matrix=[]
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [800,1440])for(const fontSize of [10,16,22]){
    await app.evaluate(({BrowserWindow},width)=>BrowserWindow.getAllWindows()[0].setSize(width,900),width)
    await page.evaluate(async({locale,theme,fontSize})=>{
      const {setLocale}=await import('/src/i18n/index.ts')
      const {useAppStore}=await import('/src/store/app.ts')
      await useAppStore.getState().setLanguage(locale);await setLocale(locale);useAppStore.getState().setTheme(theme);useAppStore.getState().setUiFontSize(fontSize)
    },{locale,theme,fontSize})
    await page.waitForFunction(({locale,theme})=>document.documentElement.lang===locale&&document.documentElement.classList.contains('dark')===(theme==='dark'),{locale,theme})
    const geometry=await page.evaluate(()=>{
      const bar=document.querySelector('.windows-titlebar').getBoundingClientRect(),shell=document.querySelector('.app-shell').getBoundingClientRect()
      const controls=[...document.querySelectorAll('.windows-titlebar button')].map(button=>{const rect=button.getBoundingClientRect();return {right:rect.right,bottom:rect.bottom,hit:document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2)===button,drag:getComputedStyle(button.closest('nav')).webkitAppRegion}})
      return {bar:{top:bar.top,bottom:bar.bottom},shell:{top:shell.top,bottom:shell.bottom},width:innerWidth,height:innerHeight,scroll:document.documentElement.scrollWidth,controls,drag:getComputedStyle(document.querySelector('.windows-titlebar')).webkitAppRegion}
    })
    assert.equal(geometry.bar.top,0);assert.equal(geometry.bar.bottom,32);assert.equal(geometry.shell.top,32);assert.equal(geometry.shell.bottom,geometry.height)
    assert.ok(geometry.controls.every(control=>control.hit&&control.right<=geometry.width-138&&control.bottom<=32&&control.drag==='no-drag'))
    assert.equal(geometry.drag,'drag');assert.ok(geometry.scroll<=geometry.width)
    matrix.push({locale,theme,width,fontSize})
    if(fontSize===16&&locale!=='en')await page.screenshot({path:path.join(screenshots,`${locale}-${theme}-${width}.png`)})
  }
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(1440,900))
  await page.evaluate(async()=>{const {useAppStore}=await import('/src/store/app.ts');useAppStore.getState().setUiFontSize(16);useAppStore.getState().setTheme('light')})
  const input=page.locator('textarea.composer-editor').first()
  await input.fill('Native edit focus 原文')
  await input.focus()
  await page.getByRole('menuitem',{name:'Edit',exact:true}).click()
  await page.waitForFunction(()=>document.querySelector('.windows-titlebar [aria-expanded="true"]'))
  assert.equal(await input.evaluate(element=>document.activeElement===element),true,'Opening the native edit menu must retain the editing target')
  await app.evaluate(({Menu,BrowserWindow})=>Menu.getApplicationMenu().items[1].submenu.closePopup(BrowserWindow.getAllWindows()[0]))
  await page.waitForFunction(()=>!document.querySelector('.windows-titlebar [aria-expanded="true"]'))
  await input.focus();await page.keyboard.press('F10')
  await page.waitForFunction(()=>document.activeElement?.getAttribute('role')==='menuitem')
  await page.keyboard.press('ArrowRight');assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Edit')
  assert.equal(await page.evaluate(()=>{
    const button=document.activeElement
    return [{key:'Enter',isComposing:true},{key:'Enter',keyCode:229}].every(options=>{
      const event=new KeyboardEvent('keydown',{...options,cancelable:true,bubbles:true});button.dispatchEvent(event);return event.defaultPrevented
    })
  }),true,'Composition Enter must suppress native button activation')
  await page.keyboard.press('Escape');assert.equal(await input.evaluate(element=>document.activeElement===element),true)
  const before=await input.inputValue()
  await page.evaluate(()=>window.dispatchEvent(new KeyboardEvent('keydown',{key:'F10',isComposing:true,bubbles:true})))
  assert.equal(await input.evaluate(element=>document.activeElement===element),true)
  assert.equal(await input.inputValue(),before)
  for(const state of [{sidebarOpen:false,inspectorOpen:false,inspectorExpanded:false},{sidebarOpen:false,inspectorOpen:true,inspectorExpanded:false},{sidebarOpen:true,inspectorOpen:true,inspectorExpanded:true}]){
    await page.evaluate(async state=>{const {useAppStore}=await import('/src/store/app.ts');useAppStore.setState(state)},state)
    await page.waitForFunction(()=>document.querySelector('.app-shell').getBoundingClientRect().top===32)
    assert.equal(await page.locator('.windows-titlebar').count(),1)
  }
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(800,900))
  await page.evaluate(async()=>{const {useAppStore}=await import('/src/store/app.ts');useAppStore.setState({inspectorExpanded:false,drawerIntent:'left'})})
  await page.waitForFunction(()=>document.querySelector('.app-shell')?.dataset.mode==='overlay'&&document.querySelector('.app-shell')?.dataset.panels==='1')
  assert.equal(await page.locator('.panel-left').evaluate(element=>element.getBoundingClientRect().top),76)
  assert.equal(await page.locator('.panel-scrim').evaluate(element=>element.getBoundingClientRect().top),76)
  await page.evaluate(async()=>{const {useAppStore}=await import('/src/store/app.ts');useAppStore.setState({drawerIntent:'none',settingsOpen:true})})
  await page.getByRole('dialog').first().waitFor()
  assert.ok(await page.locator('.windows-titlebar button').first().evaluate(button=>{const rect=button.getBoundingClientRect();return document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2)===button}),'Modal scrim must not cover native menu chrome')
  await page.evaluate(async()=>{const {useAppStore}=await import('/src/store/app.ts');useAppStore.setState({settingsOpen:false,booted:false,booting:true})})
  await page.locator('#root > [aria-busy="true"]').waitFor()
  assert.equal(await page.locator('#root > [aria-busy="true"]').evaluate(element=>element.getBoundingClientRect().top),32)
  await page.evaluate(async()=>{const {useAppStore}=await import('/src/store/app.ts');useAppStore.setState({booting:false,booted:true,bootError:'Fixture startup failure 原文'})})
  await page.locator('.desktop-boot-error').waitFor()
  assert.equal(await page.locator('.desktop-boot-error').evaluate(element=>element.getBoundingClientRect().top),32)
  await page.evaluate(async()=>{const {useAppStore}=await import('/src/store/app.ts');useAppStore.setState({bootError:null})})
  await page.locator('.app-shell').waitFor()
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(1440,900))
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].maximize())
  await page.waitForFunction(()=>navigator.windowControlsOverlay.getTitlebarAreaRect().width>0)
  assert.equal(await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].isMaximized()),true)
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].unmaximize())
  await page.waitForFunction(()=>innerWidth<1600)
  await page.screenshot({path:path.join(screenshots,'workspace-expanded.png')})
  await page.evaluate(async()=>{const {useAppStore}=await import('/src/store/app.ts');useAppStore.setState({sidebarOpen:true,inspectorOpen:true,inspectorExpanded:false})})
  await page.waitForFunction(()=>document.querySelector('.app-shell').dataset.inspectorExpanded==='0')
  await page.mouse.move(500,400)
  const native=await app.evaluate(async({desktopCapturer,BrowserWindow})=>{
    const win=BrowserWindow.getAllWindows()[0],bounds=win.getBounds()
    const sources=await desktopCapturer.getSources({types:['window'],thumbnailSize:{width:bounds.width,height:bounds.height}})
    const source=sources.find(source=>source.id===win.getMediaSourceId())
    if(!source)throw Error('Native window capture unavailable')
    return source.thumbnail.toPNG().toString('base64')
  })
  await writeFile(path.join(screenshots,'native-window.png'),Buffer.from(native,'base64'))
  assert.deepEqual(errors,[])
  await writeFile(path.join(screenshots,'acceptance.json'),JSON.stringify({passed:true,matrix,nativeControls:true,nativeMenuFocus:true,keyboard:true,drawer:true,modal:true,startup:true,platform:process.platform},null,2))
  console.log(`Windows native title bar passed: ${matrix.length} language/theme/size/font cases, native menu focus, keyboard and maximize/restore.`)
}catch(error){
  failures.push(error)
  if(page)try{await page.screenshot({path:path.join(screenshots,'failure.png')});console.error(JSON.stringify(await page.evaluate(()=>({root:[...document.querySelector('#root').children].map(e=>({tag:e.tagName,class:e.className,rect:e.getBoundingClientRect().toJSON(),height:getComputedStyle(e).height,display:getComputedStyle(e).display})),ancestors:[document.documentElement,document.body,document.querySelector('#root')].map(e=>({tag:e.tagName,rect:e.getBoundingClientRect().toJSON(),height:getComputedStyle(e).height,box:getComputedStyle(e).boxSizing}))})),null,2))}catch(diagnostic){failures.push(diagnostic)}
}
finally{
  clearTimeout(timer);lines.close()
  for(const close of [()=>app?.close(),()=>server?.close(),()=>stopChild(backend)])try{await close()}catch(error){failures.push(error)}
  if(!failures.length)await rm(profile,{recursive:true,force:true})
}
if(failures.length)throw new AggregateError(failures,'Windows title bar acceptance failed')
