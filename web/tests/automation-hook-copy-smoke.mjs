import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {AutomationsPage} from '/src/components/overlays/AutomationsPage.tsx'
import {HookFormEditor} from '/src/components/overlays/HookFormEditor.tsx'
import {readHookForm} from '/src/lib/hookForm.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t,useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.edits=[]
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',title:'Original conversation',cwd:'/fixture'}]})
function Hooks({count}){useLocale();const [text,setText]=useState(JSON.stringify({hooks:{PreToolUse:count?[{matcher:'^bash$',hooks:Array.from({length:count},(_,i)=>({type:'command',command:'echo Original-{name} 原文 '+i}))}]:[]}}));return React.createElement('section',{id:'hooks-'+count},React.createElement(HookFormEditor,{text,form:readHookForm(text),disabled:false,onChange:value=>{window.edits.push(value);setText(value)}}))}
createRoot(document.getElementById('root')).render(new URLSearchParams(location.search).get('view')==='hooks'?React.createElement('main',null,...[0,1,2].map(count=>React.createElement(Hooks,{key:count,count}))):React.createElement(AutomationsPage))
`
const original='Original-{name} “任务”'
const task={id:'task',name:original,prompt:'Original prompt 原文',session_id:'fixture',mode:'fresh',schedule:{kind:'daily',time:'09:00'},timezone:'Asia/Shanghai',due:1800000000,enabled:1,running:0,failures:0,last_error:null}
await browserScenario(async({server,browser})=>{
 for(const view of ['templates','runs','delete','hooks']){
  const page=await browser.newPage();page.setDefaultTimeout(5000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const errors=[],writes=[];page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const req=route.request(),path=new URL(req.url()).pathname
   if(req.method()!=='GET')writes.push({method:req.method(),path})
   return route.fulfill({json:path.endsWith('/preview')?{occurrences:[1800000000]}:{automations:view==='delete'&&!writes.some(item=>item.method==='DELETE')?[task]:[],runs:view==='runs'?['running','completed','failed','interrupted','constructor','Original status 原文'].map(status=>({id:status,automation_id:'task',name:'Original run '+status,session_id:null,started:1,finished:status==='running'?null:2,status,trigger:'manual',unread:0,result:JSON.stringify({text:'Original result 原文'})})):[],scheduler:{running:true,owner:true,last_error:null,max_concurrent:1}}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__automation-hook?view='+view)
  const button=async(key)=>page.getByRole('button',{name:await page.evaluate(key=>window.copy(key),key),exact:true})
  const prefix='ui.components.overlays.AutomationsPage.'
  if(view==='hooks')for(const count of [1,2])await page.locator('#hooks-'+count).getByRole('button',{name:'Delete rule 1',exact:true}).click()
  if(view==='delete')await (await button(prefix+'2f9daa828907')).click()
  if(view==='runs')await page.getByRole('tab').nth(1).click()
  if(view==='templates'){
   // 页面先以英文挂载；中文新建时必须解析当前示例，用户编辑后切语言保留原文。
   await (await button(prefix+'6bee2372805a')).waitFor()
   for(const templateLocale of ['en','zh-CN'])for(const [nameKey,promptKey] of [['582dfbcead41','2824b8898d76'],['720616f3c9b5','c148ed02f9e2'],['d3a1f32d040b','f4bf4737548a']]){
    await page.evaluate(locale=>window.locale(locale),templateLocale)
    await page.getByRole('button').filter({hasText:await page.evaluate(key=>window.copy(key),prefix+nameKey)}).click()
    assert.equal(await page.locator('textarea').inputValue(),await page.evaluate(key=>window.copy(key),prefix+promptKey))
    await page.locator('textarea').fill('User-{name} 原文')
    await page.evaluate(()=>window.locale('en'))
    assert.equal(await page.locator('textarea').inputValue(),'User-{name} 原文')
    await (await button(prefix+'d3e1da94411f')).click()
    await (await button(prefix+'6bee2372805a')).click()
    assert.equal(await page.locator('textarea').inputValue(),'')
    await (await button(prefix+'d3e1da94411f')).click()
   }
  }else{
   const before=writes.length
   for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
    await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
    if(view==='runs'){
     const keys={running:prefix+'1f0eb99b7ed0',completed:prefix+'f28461bb49c8',failed:'ui.member.status.failed',interrupted:'ui.member.status.interrupted'}
     for(const status of ['running','completed','failed','interrupted','constructor','Original status 原文']){
      const card=page.locator('article').filter({hasText:'Original run '+status})
      const key=Object.hasOwn(keys,status)?keys[status]:undefined;if(key)assert.ok(resources[locale][key]);await card.getByText(key?resources[locale][key]:status,{exact:true}).waitFor()
     }
    }else if(view==='delete')await page.getByText(locale==='en'?'Delete “'+original+'”? Run records will be preserved.':'删除“'+original+'”？运行记录会保留。',{exact:true}).waitFor()
    else{
     for(const count of [0,1,2])await page.locator('#hooks-'+count).getByText(count+' / 32 '+(locale==='en'?'commands':'个命令'),{exact:true}).waitFor()
     for(const count of [1,2]){
      const scope=page.locator('#hooks-'+count)
      await scope.getByText(locale==='en'?'Remove the '+count+(count===1?' command':' commands')+' in this rule group? Takes effect only after saving.':'移除这组规则的 '+count+' 个命令？保存后才生效。',{exact:true}).waitFor()
      await scope.getByRole('switch',{name:locale==='en'?'Enable command 1':'启用命令 1',exact:true}).waitFor()
      assert.equal(await scope.getByPlaceholder(locale==='en'?'^bash$ or ^(edit|write)$':'^bash$ 或 ^(edit|write)$').inputValue(),'^bash$')
      assert.equal(await scope.locator('textarea').first().inputValue(),'echo Original-{name} 原文 0')
     }
     assert.equal(await page.evaluate(()=>window.edits.length),0)
    }
    assert.equal(writes.length,before,'Changing language must not write')
   }
   if(view==='delete'){
    await (await button(prefix+'a3ea3c17b401')).click()
    await page.getByText(original,{exact:true}).waitFor({state:'detached'})
    assert.equal(writes.filter(item=>item.method==='DELETE').length,1)
   }else if(view==='hooks'){
    await page.locator('#hooks-2').getByRole('button',{name:'Confirm rule deletion',exact:true}).click()
    await page.locator('#hooks-2').getByText('0 / 32 commands',{exact:true}).waitFor()
    assert.equal(await page.evaluate(()=>window.edits.length),1)
    assert.deepEqual(JSON.parse(await page.evaluate(()=>window.edits[0])).hooks.PreToolUse,[])
   }
  }
  assert.deepEqual(errors,[]);await page.close();console.log(view+': current template language, original edits and explicit removal identity passed')
 }
},{entry:'/__automation-hook-entry.js',vite:{plugins:[{name:'automation-hook-fixture',resolveId(id){if(id==='/__automation-hook-entry.js')return id},load(id){if(id==='/__automation-hook-entry.js')return entry},configureServer(server){server.middlewares.use('/__automation-hook',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__automation-hook','<html><body><div id="root"></div><script type="module" src="/__automation-hook-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
