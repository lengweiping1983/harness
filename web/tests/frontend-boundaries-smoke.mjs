import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import App from '/src/App.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.configure=(language,theme)=>{
 setLocale(language);document.documentElement.className=theme
 useAppStore.setState({booted:false,booting:true,bootError:null,boot:async()=>{},refreshLanguage:async()=>{},
  sessions:[],workspaces:[{id:'fixture',path:'/fixture',name:'Fixture project'}],activeSessionId:null,activeWorkspaceId:null,
  sidebarOpen:true,inspectorOpen:true,inspectorExpanded:false,inspectorTab:'home',drawerIntent:null})
}
window.ready=()=>useAppStore.setState({booted:true,booting:false})
window.snapshot=()=>({workspace:useAppStore.getState().activeWorkspaceId,toasts:useAppStore.getState().toasts})
window.paste=(kind)=>{
 const file=new File(['invalid fixture image'],'fixture.png',{type:'image/png'})
 file.slice=()=>({arrayBuffer:()=>new Promise(resolve=>{window.releaseHead=()=>resolve(new TextEncoder().encode('invalid fixture image').buffer)})})
 const clipboard=new DataTransfer()
 if(kind!=='text')clipboard.items.add(file)
 if(kind!=='image')clipboard.setData('text/plain','Original text')
 const event=new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:clipboard})
 document.querySelector('.composer-editor').dispatchEvent(event)
 return event.defaultPrevented
}
window.configure('en','light')
createRoot(document.getElementById('root')).render(React.createElement(App))
`
const html='<!doctype html><html><body><div id="root"></div><script type="module" src="/__frontend-boundaries-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 for(const language of ['en','zh-CN'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  const strings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))
  const page=await browser.newPage({viewport:{width,height:900}}),errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>route.fulfill({status:503,json:{code:'fixture/unavailable',message:'Fixture unavailable'}}))
  await page.goto(server.resolvedUrls.local[0]+'__frontend-boundaries')
  await page.evaluate(({language,theme})=>window.configure(language,theme),{language,theme})
  await page.waitForFunction(()=>!document.querySelector('.app-shell'))
  await page.evaluate(()=>window.ready())
  const shell=page.locator('.app-shell')
  await shell.waitFor()
  await page.waitForFunction(w=>document.querySelector('.app-shell').dataset.mode===(w>=1100?'columns':'overlay'),width)
  if(width===1440){
   for(const sidebar of [true,false])for(const inspector of [true,false]){
    await page.evaluate(({sidebar,inspector})=>import('/src/store/app.ts').then(({useAppStore})=>useAppStore.setState({sidebarOpen:sidebar,inspectorOpen:inspector})),{sidebar,inspector})
    assert.deepEqual(await shell.locator(':scope > main').evaluate(element=>{
     const style=getComputedStyle(element);return [style.borderRadius,style.margin,getComputedStyle(element,'::before').display]
    }),['0px','0px','none'])
   }
   await page.evaluate(()=>import('/src/store/app.ts').then(({useAppStore})=>useAppStore.setState({sidebarOpen:true,inspectorOpen:true})))
   const divider=page.getByRole('separator')
   await divider.waitFor()
   const before=await divider.getAttribute('aria-valuenow')
   for(const props of [{isComposing:true},{keyCode:229}]){
    await divider.evaluate((element,props)=>element.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true,cancelable:true,...props})),props)
    assert.equal(await divider.getAttribute('aria-valuenow'),before)
   }
   await divider.press('ArrowLeft')
   await page.waitForFunction(before=>document.querySelector('[role="separator"]').getAttribute('aria-valuenow')!==before,before)
   await shell.evaluate(element=>element.style.width='700px')
   await page.waitForFunction(()=>document.querySelector('.app-shell').dataset.mode==='overlay')
   await shell.evaluate(element=>element.style.width='100%')
   await page.waitForFunction(()=>document.querySelector('.app-shell').dataset.mode==='columns')
   const project=page.locator('#project-sidebar section').filter({hasText:'Fixture project'}).first()
   await project.hover()
   assert.equal(typeof strings['ui.components.sidebar.ProjectSidebar.e5fc62fa56e3'],'string','Project creation must use an existing resource identity')
   const choose=project.getByRole('button',{name:strings['ui.components.sidebar.ProjectSidebar.e5fc62fa56e3'],exact:true})
   await choose.waitFor()
   assert.equal(await choose.evaluate(element=>getComputedStyle(element).opacity),'1')
   await choose.click()
   assert.equal((await page.evaluate(()=>window.snapshot())).workspace,'fixture')
  }
  const input=page.locator('.composer-editor');await input.waitFor()
  assert.equal(await page.evaluate(()=>window.paste('image')),true,'Image-only paste must be cancelled before the file header resolves')
  await page.waitForFunction(()=>typeof window.releaseHead==='function')
  await page.evaluate(()=>{window.releaseHead();window.releaseHead=null})
  assert.equal(await page.evaluate(()=>window.paste('mixed')),false,'Mixed text and image retains native text paste')
  await page.waitForFunction(()=>typeof window.releaseHead==='function')
  await page.evaluate(()=>{window.releaseHead();window.releaseHead=null})
  assert.equal(await page.evaluate(()=>window.paste('text')),false,'Plain text retains native paste')
  assert.deepEqual(errors,[])
  await page.close()
 }
 console.log('Frontend boundaries: delayed boot and container resize, divider IME/229 and normal keys, project selection and synchronous paste cancellation across eight layouts passed')
},{entry:'/__frontend-boundaries-entry.js',vite:{plugins:[{name:'frontend-boundaries-fixture',resolveId(id){if(id==='/__frontend-boundaries-entry.js')return id},load(id){if(id==='/__frontend-boundaries-entry.js')return entry},configureServer(server){server.middlewares.use('/__frontend-boundaries',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__frontend-boundaries',html))}catch(error){next(error)}})}}]}})
