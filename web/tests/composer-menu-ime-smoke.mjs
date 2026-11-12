import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ComposerAddMenu} from '/src/components/chat/ComposerAddMenu.tsx'
import {PermissionMenu} from '/src/components/chat/PermissionMenu.tsx'
import {ScreenshotButton} from '/src/components/chat/ScreenshotButton.tsx'
import {AttachmentDraftTrack} from '/src/components/chat/AttachmentDraft.tsx'
import {useAppStore} from '/src/store/app.ts'
import {usePermissionStore} from '/src/store/permissions.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.actions=0
useAppStore.setState({activeSessionId:'fixture'})
usePermissionStore.setState({load:async()=>{},policy:'request_approval',sessionId:'fixture',scope:'fixture',inherited:false,loading:false,busy:false})
const noop=()=>window.actions++
createRoot(document.getElementById('root')).render(React.createElement('main',null,
 React.createElement('div',{'data-control':'add'},React.createElement(ComposerAddMenu,{sessionId:'fixture',draft:'',onChange:noop,focusEditor:noop,onAddFiles:noop,onReference:noop,mode:'normal',modeBusy:false,onSelectMode:noop})),
 React.createElement('div',{'data-control':'permissions'},React.createElement(PermissionMenu,{connection:'open'})),
 React.createElement('div',{'data-control':'screenshot'},React.createElement(ScreenshotButton,{addFiles:async()=>noop(),focusEditor:noop})),
 React.createElement(AttachmentDraftTrack,{items:[{localId:'image',name:'Original image',mediaType:'image/png',size:1,isImage:true,previewUrl:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lVQAAAAASUVORK5CYII='}],onRemove:noop})))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[]
 page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)})
 await page.route('**/api/v1/**',route=>{requests.push(new URL(route.request().url()).pathname);if(requests.at(-1)==='/api/v1/screenshot/capabilities')return route.fulfill({json:{native:false,hideWindow:false,reason:null}});return route.fulfill({status:500})})
 await page.goto(server.resolvedUrls.local[0]+'__composer-menu-ime')
 await page.locator('[data-control="add"] button').first().waitFor()
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
  for(const control of ['add','permissions']){
   const anchor=page.locator('[data-control="'+control+'"] button[aria-haspopup="menu"]')
   await anchor.click()
   const items=page.getByRole(control==='add'?'menuitem':'menuitemradio')
   if(control==='add'){
    const expected=await page.evaluate(()=>['files','reference','plan','goal','agents','skills','connectors'].map(key=>window.copy('ui.composerAdd.'+key)))
    assert.deepEqual(await items.allTextContents(),expected)
   }
   await items.first().focus()
   for(const key of ['ArrowDown','Home','End','Escape'])for(const mode of ['composing','229']){
    const result=await items.first().evaluate((node,{key,mode})=>{
     node.focus();const event=new KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,isComposing:mode==='composing',keyCode:mode==='229'?229:0})
     node.dispatchEvent(event);return{prevented:event.defaultPrevented,focused:document.activeElement===node}
    },{key,mode})
    assert.deepEqual(result,{prevented:false,focused:true},control+' '+key+' '+mode)
    assert.equal(await anchor.getAttribute('aria-expanded'),'true')
   }
   await items.first().focus();await items.first().press('ArrowDown')
   assert.equal(await items.nth(1).evaluate(node=>document.activeElement===node),true)
   await page.keyboard.press('Escape');assert.equal(await anchor.getAttribute('aria-expanded'),'false')
  }
  const screenshot=page.locator('[data-control="screenshot"] button[aria-haspopup="menu"]')
  for(const key of ['ArrowDown','ArrowUp'])for(const mode of ['composing','229']){
   const prevented=await screenshot.evaluate((node,{key,mode})=>{node.focus();const event=new KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,isComposing:mode==='composing',keyCode:mode==='229'?229:0});node.dispatchEvent(event);return event.defaultPrevented},{key,mode})
   assert.equal(prevented,false);assert.equal(await screenshot.getAttribute('aria-expanded'),'false')
  }
  await screenshot.focus();await screenshot.press('ArrowDown')
  const screenshotItem=page.getByRole('menuitem');await screenshotItem.waitFor()
  for(const key of ['Escape','Tab'])for(const mode of ['composing','229']){
   const prevented=await screenshotItem.evaluate((node,{key,mode})=>{node.focus();const event=new KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,isComposing:mode==='composing',keyCode:mode==='229'?229:0});node.dispatchEvent(event);return event.defaultPrevented},{key,mode})
   assert.equal(prevented,false);assert.equal(await screenshot.getAttribute('aria-expanded'),'true')
  }
  await page.keyboard.press('Escape');assert.equal(await screenshot.getAttribute('aria-expanded'),'false')
  const image=page.getByRole('listitem')
  for(const mode of ['composing','229']){
   const prevented=await image.evaluate((node,mode)=>{node.focus();const event=new KeyboardEvent('keydown',{key:'F10',shiftKey:true,bubbles:true,cancelable:true,isComposing:mode==='composing',keyCode:mode==='229'?229:121});node.dispatchEvent(event);return event.defaultPrevented},mode)
   assert.equal(prevented,false);assert.equal(await page.getByRole('menu').count(),0)
  }
  await image.focus();await image.press('Shift+F10');await page.getByRole('menu').waitFor()
  await page.keyboard.press('Escape');await page.getByRole('menu').waitFor({state:'detached'})
 }
 assert.equal(await page.evaluate(()=>window.actions),0);assert.deepEqual(requests,['/api/v1/screenshot/capabilities']);assert.deepEqual(errors,[])
 await page.close();console.log('Composer menus: composition and 229 preserve focus/open state; ordinary navigation and Escape work across languages, themes and widths')
},{entry:'/__composer-menu-ime-entry.js',vite:{plugins:[{name:'composer-menu-ime',resolveId(id){if(id==='/__composer-menu-ime-entry.js')return id},load(id){if(id==='/__composer-menu-ime-entry.js')return entry},configureServer(server){server.middlewares.use('/__composer-menu-ime',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__composer-menu-ime','<html><body><div id="root"></div><script type="module" src="/__composer-menu-ime-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
