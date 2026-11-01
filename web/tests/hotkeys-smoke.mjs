import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import path from 'node:path'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {useHotkeys} from '/src/hooks/useHotkeys.ts'
import {useAppStore} from '/src/store/app.ts'
import {SettingsDialog} from '/src/components/overlays/SettingsDialog.tsx'
import {ShortcutsDialog} from '/src/components/overlays/CommandPalette.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
const params = new URLSearchParams(location.search)
setLocale(params.get('locale'))
document.documentElement.classList.toggle('dark', params.get('theme') === 'dark')
window.created = 0
window.readSidebar = () => useAppStore.getState().sidebarOpen
window.showView = view => useAppStore.setState({settingsOpen:view === 'settings',settingsTarget:'shortcuts',shortcutsOpen:view === 'help'})
function Fixture() {
  useHotkeys({onNewSession:()=>{window.created++}})
  return React.createElement(React.Fragment,null,
    React.createElement('button',{'data-invoker':true},'Focus'),
    React.createElement('textarea',{'aria-label':'Draft'}),
    React.createElement('div',{className:'xterm'},React.createElement('textarea',{'aria-label':'Terminal'})),
    React.createElement(SettingsDialog),React.createElement(ShortcutsDialog))
}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
window.showView('settings')
`
const html = '<!doctype html><html><body><div id="root"></div><script type="module" src="/__hotkeys-entry.js"></script></body></html>'
await browserScenario(async ({server,browser}) => {
  const screenshots = process.env.HARNESS_TEST_SCREENSHOTS
  if (screenshots) await mkdir(screenshots,{recursive:true})
  for (const platform of ['MacIntel','Win32']) {
    for (const locale of ['zh-CN','en']) for (const theme of ['light','dark']) for (const width of [390,1440]) {
      const page = await browser.newPage({viewport:{width,height:900}})
      await page.addInitScript(platform => Object.defineProperty(navigator,'platform',{get:()=>platform}),platform)
      const errors=[]
      page.on('pageerror',error=>errors.push(error.message))
      await page.goto(server.resolvedUrls.local[0]+`__hotkeys?locale=${locale}&theme=${theme}`)
      const name=locale==='zh-CN'?'新建对话':'New conversation'
      const mod=platform==='MacIntel'?'⌘':'Ctrl'
      const primary=platform==='MacIntel'?'Meta':'Control'
      await page.getByText(name,{exact:true}).waitFor()
      assert.deepEqual(await page.getByText(name,{exact:true}).evaluate(el=>[...el.parentElement.querySelectorAll('kbd')].map(key=>key.textContent)),[mod,'Shift','U'])
      const terminal=locale==='zh-CN'?'终端工作台':'Terminal workspace'
      assert.deepEqual(await page.getByText(terminal,{exact:true}).evaluate(el=>[...el.parentElement.querySelectorAll('kbd')].map(key=>key.textContent)),['Ctrl','`'])
      if (screenshots) await page.screenshot({path:path.join(screenshots,`${platform}-${locale}-${theme}-${width}.png`),fullPage:true})
      await page.keyboard.press(primary+'+Shift+u')
      assert.equal(await page.evaluate(()=>window.created),0,'A settings overlay must not create a session')
      await page.evaluate(()=>window.showView('help'))
      await page.getByText(name,{exact:true}).waitFor()
      assert.deepEqual(await page.getByText(name,{exact:true}).evaluate(el=>[...el.closest('li').querySelectorAll('kbd')].map(key=>key.textContent)),[mod,'Shift','U'])
      await page.keyboard.press(primary+'+Shift+u')
      assert.equal(await page.evaluate(()=>window.created),0,'A help overlay must not create a session')
      await page.evaluate(()=>window.showView('none'))
      await page.getByRole('textbox',{name:'Draft'}).fill('keep draft')
      await page.keyboard.press('n')
      assert.equal(await page.evaluate(()=>window.created),0)
      await page.keyboard.press(primary+'+Shift+u')
      assert.equal(await page.evaluate(()=>window.created),1,'The primary chord must work while composing')
      assert.equal(await page.getByRole('textbox',{name:'Draft'}).inputValue(),'keep draftn')
      await page.locator('[data-invoker]').focus()
      await page.keyboard.press('n')
      assert.equal(await page.evaluate(()=>window.created),1,'Bare N must not create a session')
      await page.keyboard.press(primary+'+Shift+n')
      assert.equal(await page.evaluate(()=>window.created),1)
      await page.evaluate(primary=>window.dispatchEvent(new KeyboardEvent('keydown',{key:'u',shiftKey:true,isComposing:true,[primary==='Meta'?'metaKey':'ctrlKey']:true,cancelable:true})),primary)
      assert.equal(await page.evaluate(()=>window.created),1,'IME composition must retain the key')
      await page.keyboard.press(primary+'+Shift+u')
      assert.equal(await page.evaluate(()=>window.created),2)
      await page.getByRole('textbox',{name:'Terminal'}).focus()
      const sidebarBefore = await page.evaluate(()=>window.readSidebar())
      await page.keyboard.press('Control+b')
      assert.equal(await page.evaluate(()=>window.readSidebar()),sidebarBefore,'Control+B inside the terminal belongs to the shell')
      assert.deepEqual(errors,[])
      await page.close()
    }
  }
  console.log('Hotkeys: platform symbols, settings/help, primary+Shift+U, draft preservation, bare N, overlays and IME across locale/theme/width variants passed')
},{entry:'/__hotkeys-entry.js',vite:{plugins:[{name:'hotkeys-fixture',resolveId(id){if(id==='/__hotkeys-entry.js')return id},load(id){if(id==='/__hotkeys-entry.js')return entry},configureServer(server){server.middlewares.use('/__hotkeys',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__hotkeys',html))}catch(error){next(error)}})}}]}})
