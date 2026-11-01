import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Inspector} from '/src/components/inspector/Inspector.tsx'
import {CommandPalette} from '/src/components/overlays/CommandPalette.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useChatStore,emptySession} from '/src/store/chat.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.reads=[];window.queueReads=[]
window.fetch=async(path,options={})=>{
 window.reads.push({path:String(path),method:options.method||'GET'})
 if(String(path).endsWith('/terminal/types'))return Response.json({backends:['cmd'],default_type:'cmd',enabled:true,managed_runtime:false})
 if(String(path).includes('/trace?'))return Response.json({session_id:'fixture',entries:[],has_more:false,next_after:null,context_missing:false})
 if(String(path).includes('/runs?'))return Response.json({runs:[],has_more:false,next_before:null})
 if(String(path).endsWith('/queue'))return new Promise(resolve=>window.queueReads.push(resolve))
 throw Error('Unexpected navigation request: '+path)
}
window.answerQueue=failed=>window.queueReads.shift()(failed?Response.json({message:'Queue unavailable'},{status:503}):Response.json({runs:[]}))
window.tab=value=>useAppStore.getState().setInspectorTab(value)
window.commands=()=>useAppStore.getState().setCommandOpen(true)
window.noSession=()=>useAppStore.setState({activeSessionId:null})
useAppStore.setState({activeSessionId:'fixture',inspectorTab:'home',workspace:null,sessions:[]})
useChatStore.setState({sessions:{fixture:emptySession()}})
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement('main',{className:'h-screen w-full max-w-2xl'},React.createElement(Inspector)),React.createElement(CommandPalette)))
`

await browserScenario(async ({server,browser})=>{
  const page=await browser.newPage(), errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.clock.install()
  await page.goto(server.resolvedUrls.local[0]+'__inspector-navigation')
  await page.waitForFunction(()=>window.reads.length===1)
  // 暂停点取自浏览器自身时钟，避免宿主取整或调度差异产生倒退。
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000))
  const panel=page.locator('#inspector-tab-content'), select=page.locator('.inspector-section-switcher select')
  const labels=locale=>locale==='en'?{progress:'Progress',details:'Execution details',review:'Changes',resources:'Files, preview and terminal',help:'Learning and help',log:'Request and tool log',history:'Task history'}:{progress:'进度',details:'执行详情',review:'变更',resources:'文件、预览与终端',help:'学习与帮助',log:'请求与工具日志',history:'任务历史'}
  const matrix=async check=>{
    for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
      await page.setViewportSize({width,height:900})
      await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
      await check(locale,labels(locale))
      assert.equal(await panel.evaluate(node=>node.scrollWidth<=node.clientWidth+1),true,'Navigation must fit narrow and wide panels')
    }
  }
  await matrix(async(locale,copy)=>{
    const details=panel.locator('details').filter({has:page.locator('summary').getByText(copy.details,{exact:true})})
    assert.equal(await details.evaluate(node=>node.open),true,'Launcher groups start expanded')
    assert.deepEqual(await panel.locator('details').evaluateAll(nodes=>nodes.map(node=>node.querySelector('nav')?.getAttribute('aria-label'))),[copy.progress,copy.details,copy.review,copy.resources,copy.help])
    assert.deepEqual(await select.locator('optgroup').evaluateAll(nodes=>nodes.map(node=>node.label)),[copy.progress,copy.details,copy.review,copy.resources,copy.help])
    assert.equal(await select.locator('option[value=prompts]').textContent(),copy.log)
    assert.equal(await select.locator('option[value=prompts]').count(),1)
    const summary=details.locator('summary');await summary.focus();await page.keyboard.press('Enter')
    assert.equal(await details.evaluate(node=>node.open),false)
    await summary.focus();await page.keyboard.press('Enter')
    await details.getByRole('button',{name:copy.log,exact:true}).waitFor()
    assert.equal(await details.evaluate(node=>node.open),true)
    assert.equal(await page.evaluate(()=>window.reads.length),1,'Opening the disclosure must not load diagnostic panels')
    if(locale==='zh-CN'&&process.env.HARNESS_TEST_SCREENSHOT)await panel.screenshot({path:process.env.HARNESS_TEST_SCREENSHOT})
  })
  const details=panel.locator('details').filter({has:page.locator('summary').getByText('Execution details',{exact:true})})
  if ((await details.getAttribute('open')) === null) await details.locator('summary').click()
  await details.getByRole('button',{name:'Request and tool log',exact:true}).click()
  await panel.getByText('No model requests, tool calls or subagent activity yet.',{exact:true}).waitFor()
  const logReads=await page.evaluate(()=>window.reads.length)
  await matrix(async(locale,copy)=>{
    await page.getByRole('tab',{name:copy.log,exact:true}).waitFor()
    assert.equal(await panel.getAttribute('aria-label'),copy.log)
    await panel.getByRole('region',{name:copy.log,exact:true}).waitFor()
    assert.equal(await select.locator('option[value=prompts]').textContent(),copy.log)
    await panel.getByRole('button',{name:locale==='en'?'Refresh request and tool log':'刷新请求与工具日志',exact:true}).waitFor()
    await panel.getByText(locale==='en'?'No model requests, tool calls or subagent activity yet.':'暂无模型请求、工具调用或子智能体活动。',{exact:true}).waitFor()
    assert.equal(await page.evaluate(()=>window.reads.length),logReads)
  })
  await page.evaluate(()=>window.commands())
  for(const locale of ['en','zh-CN','en']){
    await page.evaluate(locale=>window.locale(locale),locale)
    const title=locale==='en'?'Open the "Request and tool log" panel':'打开“请求与工具日志”面板'
    await page.locator('[data-command-search]').fill(labels(locale).log)
    await page.getByRole('option').getByText(title,{exact:true}).waitFor()
  }
  await page.keyboard.press('Escape')
  await select.selectOption('queue')
  await page.waitForFunction(()=>window.queueReads.length===1)
  for(const locale of ['en','zh-CN','en']){
    await page.evaluate(locale=>window.locale(locale),locale)
    await panel.getByRole('heading',{name:labels(locale).history,exact:true}).waitFor()
    await panel.getByText(locale==='en'?'Reading task history…':'正在读取任务历史…',{exact:true}).waitFor()
  }
  await page.evaluate(()=>window.answerQueue(false))
  await matrix(async(locale,copy)=>{
    await panel.getByRole('region',{name:copy.history,exact:true}).waitFor()
    await panel.getByRole('heading',{name:copy.history,exact:true}).waitFor()
    await panel.getByRole('navigation',{name:locale==='en'?'Task history pagination':'任务历史分页',exact:true}).waitFor()
    await panel.getByText(locale==='en'?'There are no task records on this page.':'这一页没有任务记录。',{exact:true}).waitFor()
    assert.equal(await panel.getByRole('heading',{name:copy.log,exact:true}).count(),0)
  })
  await select.selectOption('prompts');await select.selectOption('queue')
  await page.waitForFunction(()=>window.queueReads.length===1)
  await page.evaluate(()=>window.answerQueue(true))
  for(const locale of ['en','zh-CN','en']){
    await page.evaluate(locale=>window.locale(locale),locale)
    await panel.getByRole('heading',{name:locale==='en'?'Task history':'任务历史',exact:true}).waitFor()
    await panel.getByRole('alert').waitFor()
  }
  await page.evaluate(()=>window.noSession())
  for(const locale of ['en','zh-CN','en']){
    await page.evaluate(locale=>window.locale(locale),locale)
    await panel.getByText(locale==='en'?'Select a conversation to view its task queue and task history.':'选择一个对话后查看任务队列与任务历史。',{exact:true}).waitFor()
  }
  await select.selectOption('prompts')
  for(const locale of ['en','zh-CN','en']){
    await page.evaluate(locale=>window.locale(locale),locale)
    await panel.getByText(locale==='en'?'Select a conversation to view its request and tool log.':'选择一个对话后查看请求与工具日志。',{exact:true}).waitFor()
  }
  assert.equal(await page.evaluate(()=>window.reads.some(row=>row.method!=='GET')),false)
  assert.deepEqual(errors,[])
  console.log('Inspector navigation: consistent expanded groups, distinct history/log names across launcher/tabs/selector/commands, loading/empty/error and en→zh→en themes/widths passed')
},{entry:'/__inspector-navigation-entry.js',vite:{cacheDir:'../output/inspector-navigation-vite',plugins:[{
  name:'inspector-navigation-fixture',resolveId(id){if(id==='/__inspector-navigation-entry.js')return id},load(id){if(id==='/__inspector-navigation-entry.js')return entry},
  configureServer(server){server.middlewares.use('/__inspector-navigation',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__inspector-navigation','<html><body><div id="root"></div><script type="module" src="/__inspector-navigation-entry.js"></script></body></html>'))}catch(error){next(error)}})},
}]}})
