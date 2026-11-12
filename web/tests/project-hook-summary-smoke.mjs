import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ProjectHooksPage} from '/src/components/overlays/ProjectHooksPage.tsx'
import {GeneralPage} from '/src/components/overlays/SettingsDialog.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
useAppStore.setState({activeWorkspaceId:'/fixture',workspaces:[{id:'/fixture',name:'Original project',path:'/fixture'}],settingsWorkspaceRoot:'/fixture'})
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement(ProjectHooksPage),React.createElement(GeneralPage)))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],writes=[];let reads=0
 page.on('pageerror',error=>errors.push(error.message));await page.clock.install();await page.clock.pauseAt(new Date(Date.now()+1000))
 const rows=['PreToolUse','Stop'].map((event,index)=>({id:'rule'+index,event,matcher:'Original matcher',command:'echo Original 原文',timeout:3,label:'Original label '+index,enabled:true}))
 const runs=rows.map((row,index)=>({...row,id:'run'+index,session_id:'owner',tool:index?'':'Original tool',status:'passed',reason:'',stdout:'Original output 原文',stderr:'',started:1,duration_ms:12}))
 await page.route('**/api/v1/**',route=>{
  if(route.request().method()!=='GET'){writes.push(route.request().url());return route.fulfill({status:500})}
  reads++
  return route.fulfill({json:new URL(route.request().url()).pathname.endsWith('/plugin-packages')?{packages:[]}:{root:'/fixture',source:'Original source',fingerprint:'a'.repeat(64),trusted:true,document:{hooks:{}},rows,runs,error:''}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__project-hook-summary')
 const text=(key,params)=>page.evaluate(({key,params})=>window.copy(key,params),{key,params})
 await page.getByText(await text('ui.hooks.ruleCount',{count:2}),{exact:true}).waitFor()
 const initialReads=reads
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  await page.getByRole('tab',{name:await text('ui.components.overlays.ProjectHooksPage.e6e947abb969'),exact:true}).click()
  await page.getByText(await text('ui.hooks.ruleCount',{count:2}),{exact:true}).waitFor()
  await page.getByText(locale==='en'?'Preview which commands the current draft would trigger without running scripts. Matchers use a case-sensitive regex against original tool names; empty or * matches all.':'预览当前草稿会触发哪些命令，不执行脚本。matcher 对工具原名使用区分大小写的正则表达式；空串或 * 匹配全部。',{exact:true}).waitFor()
  await page.getByText(await text('ui.components.overlays.SettingsDialog.c92eca635bc8'),{exact:true}).waitFor()
  const event=await text('ui.lib.projectHooks.aa173c3d9fab')
  await page.getByText(await text('ui.hooks.matchedRuleSummary',{event,matcher:'Original matcher',seconds:3}),{exact:true}).waitFor()
  await page.getByText(await text('ui.hooks.ruleSummary',{event:await text('ui.lib.projectHooks.2e2a6cf056dd'),seconds:3}),{exact:true}).waitFor()
  await page.getByRole('tab',{name:await text('ui.components.overlays.ProjectHooksPage.6be3020c8daa',{p0:2}),exact:true}).click()
  for(const run of runs){
   const expected=await page.evaluate(run=>window.copy(run.tool?'ui.hooks.runWithTool':'ui.hooks.runSummary',{event:run.event,tool:run.tool,milliseconds:run.duration_ms,time:new Date(run.started*1000).toLocaleTimeString(document.documentElement.lang)}),run)
   await page.getByText(expected,{exact:true}).waitFor()
  }
  const tabs=page.getByRole('tablist').getByRole('tab')
  const panel=page.getByRole('tabpanel')
  await tabs.last().focus()
  for(const key of ['ArrowLeft','ArrowRight','Home','End']){
   await page.keyboard.press(key)
   const selected=page.getByRole('tab',{selected:true})
   const expected=key==='ArrowLeft'||key==='Home'?0:1
   assert.equal(await tabs.nth(expected).getAttribute('aria-selected'),'true')
   assert.equal(await selected.evaluate(el=>el===document.activeElement),true)
   assert.equal(await panel.getAttribute('aria-labelledby'),await selected.getAttribute('id'))
   assert.equal(await selected.getAttribute('aria-controls'),await panel.getAttribute('id'))
  }
  await tabs.last().evaluate(el=>{
   for(const event of [new KeyboardEvent('keydown',{key:'ArrowLeft',isComposing:true,bubbles:true}),new KeyboardEvent('keydown',{key:'Home',keyCode:229,bubbles:true}),new KeyboardEvent('keydown',{key:'ArrowLeft',ctrlKey:true,bubbles:true})])el.dispatchEvent(event)
  })
  assert.equal(await tabs.last().getAttribute('aria-selected'),'true')
  if(locale==='zh-CN'&&!dark&&width===1440&&process.env.HARNESS_TEST_HOOK_TABS_SCREENSHOT)await page.locator('#root > div').first().screenshot({path:process.env.HARNESS_TEST_HOOK_TABS_SCREENSHOT})
  assert.equal(reads,initialReads);assert.deepEqual(writes,[])
 }
 await page.getByRole('tab',{name:await text('ui.components.overlays.ProjectHooksPage.e6e947abb969'),exact:true}).click()
 await page.getByRole('button',{name:await text('ui.components.overlays.ProjectHooksPage.6e814135ad1b'),exact:true}).click()
 const editor=page.getByRole('textbox',{name:await text('ui.components.overlays.ProjectHooksPage.bcb5a89bfebf'),exact:true})
 await editor.fill('Original damaged JSON')
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  const reason=await text('ui.lib.hookForm.5d6f65ba77be')
  await page.getByRole('alert').getByText(await text('ui.hooks.formError',{reason}),{exact:true}).waitFor()
  assert.equal(await page.getByRole('textbox',{name:await text('ui.components.overlays.ProjectHooksPage.bcb5a89bfebf'),exact:true}).inputValue(),'Original damaged JSON')
 }
 await page.getByRole('tab').first().focus();await page.keyboard.press('End');await page.keyboard.press('Home')
 assert.equal(await editor.inputValue(),'Original damaged JSON','Switching tabs preserves the configuration draft')
 await editor.fill(JSON.stringify({hooks:{PreToolUse:[{matcher:'Original matcher',hooks:[{type:'command',command:'Original command',timeout:0}]}]}}))
 await page.getByRole('button',{name:await text('ui.components.overlays.ProjectHooksPage.a2a10c1119f6'),exact:true}).click()
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  await page.getByRole('alert').getByText(await text('ui.hooks.formIssues',{count:1}),{exact:true}).waitFor()
  assert.equal(await page.getByRole('button',{name:await text('ui.components.overlays.ProjectHooksPage.6e584e3d5ce6'),exact:true}).isDisabled(),true)
 }
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  await page.getByText(locale==='en'
   ?'Check action; can block execution. Use a case-sensitive regex; empty or * matches all. SessionStart/End match the source/end reason. Commands in the same group run in display order.'
   :'检查操作，可以拦截。使用区分大小写的正则表达式；空串或 * 匹配全部。SessionStart/End 匹配来源／结束原因。同组命令按显示顺序执行。',{exact:true}).waitFor()
 }
 assert.equal(reads,initialReads);assert.deepEqual(writes,[])
 assert.deepEqual(errors,[]);await page.close();console.log('Project Hook summaries: complete bilingual counts, rule duration and runs; original protocol values retained without operations')
},{entry:'/__project-hook-summary-entry.js',vite:{plugins:[{name:'project-hook-summary',enforce:'pre',resolveId(id){if(id==='/__project-hook-summary-entry.js')return id},async load(id){if(id.endsWith('/SettingsDialog.tsx'))return await readFile(id,'utf8')+'\nexport {GeneralPage}\n';if(id==='/__project-hook-summary-entry.js')return entry},configureServer(server){server.middlewares.use('/__project-hook-summary',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__project-hook-summary','<html><body><div id="root"></div><script type="module" src="/__project-hook-summary-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
