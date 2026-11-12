import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Inspector} from '/src/components/inspector/Inspector.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useChatStore,emptySession} from '/src/store/chat.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.reads=[];window.queueReads=[]
window.fetch=async(path,options={})=>{
 window.reads.push({path:String(path),method:options.method||'GET'})
 if(String(path).endsWith('/plans'))return Response.json({plans:[]})
 if(String(path).includes('/runs?'))return Response.json({runs:[{id:'history-entry',session_id:'fixture',text:'Original history task 原文',status:'completed',created_at:1}],has_more:false,next_before:null})
 if(String(path).endsWith('/queue'))return new Promise(resolve=>window.queueReads.push(resolve))
 throw Error('Unexpected fixture request: '+path)
}
window.answerQueue=(count,failed=false)=>window.queueReads.shift()(failed?Response.json({message:'Queue unavailable'},{status:503}):Response.json({runs:Array.from({length:count},(_,index)=>({id:'entry-'+index,text:'Original task 原文 '+index,edit_version:'a'.repeat(64)}))}))
window.queue=()=>useAppStore.getState().setInspectorTab('queue')
useAppStore.setState({activeSessionId:'fixture',inspectorTab:'todos'})
useChatStore.setState({sessions:{fixture:{...emptySession(),todos:[{id:'todo',content:'Original checklist 原文',status:'completed'}]}}})
createRoot(document.getElementById('root')).render(React.createElement('main',{className:'h-screen w-full max-w-2xl bg-surface text-ink'},React.createElement(Inspector)))
`

await browserScenario(async ({server,browser}) => {
  const page = await browser.newPage({viewport:{width:1440,height:900}}), errors = []
  page.on('pageerror',error=>errors.push(error.message))
  await page.clock.install()
  await page.goto(server.resolvedUrls.local[0]+'__inspector-copy')
  await page.getByText('Plans are saved here. You can revisit approved versions and feedback.',{exact:true}).waitFor()
  await page.clock.pauseAt(new Date())
  const panel = page.locator('#inspector-tab-content')
  const matrix = async check => {
    for (const locale of ['en','zh-CN','en']) for (const dark of [false,true]) for (const width of [390,1440]) {
      await page.setViewportSize({width,height:900})
      await page.evaluate(async ({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
      await check(locale)
      assert.equal(await panel.evaluate(node=>node.scrollWidth<=node.clientWidth+1),true,'Changed inspector copy must fit the panel')
    }
  }
  await matrix(async locale=>{
    await panel.getByRole('heading',{name:locale==='en'?'Plan':'计划',exact:true}).waitFor()
    await panel.getByText(locale==='en'?'Plans are saved here. You can revisit approved versions and feedback.':'计划将保存在这里，可回看批准版本和修改意见。',{exact:true}).waitFor()
    await panel.getByText(locale==='en'?'Implementation progress reported by the model; a checked item does not mean verification passed.':'模型声明的实施进度；已勾选不等于验证通过。',{exact:true}).waitFor()
    await panel.getByText('Original checklist 原文',{exact:true}).waitFor()
  })
  assert.equal(await page.evaluate(()=>window.reads.length),1,'Language changes must preserve the loaded plan list')
  await page.evaluate(()=>window.queue())
  await page.waitForFunction(()=>window.queueReads.length===1)
  const heading = page.locator('[data-queue-heading]')
  const countTitle = (locale,count)=>locale==='en'?('Pending task queue ('+count+(count===1?' item)':' items)')):('待执行任务队列（'+count+' 项）')
  await matrix(async locale=>assert.equal(await heading.innerText(),locale==='en'?'Pending task queue':'待执行任务队列'))
  for (const count of [0,1,3]) {
    if (count) {
      await panel.getByRole('button',{name:'Refresh',exact:true}).click()
      await page.waitForFunction(()=>window.queueReads.length===1)
    }
    await page.evaluate(count=>window.answerQueue(count),count)
    await heading.getByText(countTitle('en',count),{exact:true}).waitFor()
    const reads = await page.evaluate(()=>window.reads.length)
    await matrix(async locale=>{
      assert.equal(await heading.innerText(),countTitle(locale,count))
      await panel.getByText(locale==='en'?'At most 25 items per page; expand to view details.':'每页最多 25 项；展开查看详情。',{exact:true}).waitFor()
      if (!count) await panel.getByText(locale==='en'?'No tasks waiting.':'暂无待执行任务。',{exact:true}).waitFor()
      else for (let index=0;index<count;index++) await panel.getByText('Original task 原文 '+index,{exact:true}).waitFor()
      assert.equal(await page.evaluate(()=>window.reads.length),reads,'Language changes must not reload the queue')
      if (!count && locale==='zh-CN' && !await page.locator('html').evaluate(node=>node.classList.contains('dark')) && process.env.HARNESS_TEST_SCREENSHOT) await panel.screenshot({path:process.env.HARNESS_TEST_SCREENSHOT})
    })
  }
  await panel.getByRole('button',{name:'Refresh',exact:true}).click()
  await page.waitForFunction(()=>window.queueReads.length===1)
  await page.evaluate(()=>window.answerQueue(0,true))
  await panel.getByRole('alert').waitFor()
  assert.equal(await heading.innerText(),countTitle('en',3),'A failed refresh must preserve the last confirmed count')
  await panel.getByText('Original history task 原文',{exact:true}).first().waitFor()
  await heading.focus()
  assert.equal(await heading.evaluate(node=>node===document.activeElement),true)
  assert.equal(await page.evaluate(()=>window.reads.some(row=>row.method!=='GET')),false)
  assert.deepEqual(errors,[])
  console.log('Inspector copy: plans, checklist, queue loading/counts/empty/error, en→zh→en, themes, widths and focus passed')
},{entry:'/__inspector-copy-entry.js',vite:{cacheDir:'../output/inspector-copy-vite',plugins:[{
  name:'inspector-copy-fixture',resolveId(id){if(id==='/__inspector-copy-entry.js')return id},load(id){if(id==='/__inspector-copy-entry.js')return entry},
  configureServer(server){server.middlewares.use('/__inspector-copy',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__inspector-copy','<html><body><div id="root"></div><script type="module" src="/__inspector-copy-entry.js"></script></body></html>'))}catch(error){next(error)}})},
}]}})
