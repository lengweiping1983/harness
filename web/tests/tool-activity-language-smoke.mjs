import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ToolBlock} from '/src/components/chat/blocks/ToolBlock.tsx'
import {setLocale,useLocale} from '/src/i18n/index.ts'
import {formatRelativeTime} from '/src/lib/utils.ts'
import '/src/styles.css'
window.locale=setLocale;setLocale('en')
function Fixture(){useLocale();return React.createElement('main',null,
 ...['spawn_agent','followup_task'].flatMap(name=>['running','error','ok'].map(status=>React.createElement('section',{key:name+status,id:name+'-'+status},React.createElement(ToolBlock,{block:{kind:'tool',id:name+status,callId:name+status,name,status,at:1,input:{description:'Original-{name} 原文'},output:'Original output 原文'}})))),
 ...[45,60,120,3600,7200,86400,172800].map(seconds=>React.createElement('span',{key:seconds,id:'time-'+seconds},formatRelativeTime(Date.now()-seconds*1000))))}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(5000)
 const errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().method());return route.fulfill({status:500})})
 await page.goto(server.resolvedUrls.local[0]+'__tool-activity')
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const name of ['spawn_agent','followup_task'])for(const status of ['running','error','ok']){
   const scope=page.locator('#'+name+'-'+status)
   const action=locale==='en'?(name==='spawn_agent'?'Start a subagent':'Continue the Agent task'):(name==='spawn_agent'?'启动子智能体':'继续智能体任务')
   const label=locale==='en'?(status==='running'?'In progress: ':status==='error'?'Failed: ':'Completed: ')+action:status==='running'?'正在'+action:status==='error'?action+'失败':'已'+action
   await scope.getByText(label,{exact:true}).waitFor()
   await scope.getByText('Original-{name} 原文',{exact:true}).first().waitFor()
   const button=scope.locator('button[aria-expanded]').first()
   if(await button.getAttribute('aria-expanded')!=='true')await button.click()
   await scope.getByText(name,{exact:true}).waitFor()
   if(status!=='running')await scope.getByText('Original output 原文',{exact:true}).waitFor()
  }
  for(const [seconds,en,zh] of [[45,'Just now','刚刚'],[60,'1 minute ago','1 分钟前'],[120,'2 minutes ago','2 分钟前'],[3600,'1 hour ago','1 小时前'],[7200,'2 hours ago','2 小时前'],[86400,'1 day ago','1 天前'],[172800,'2 days ago','2 天前']])await page.locator('#time-'+seconds).getByText(locale==='en'?en:zh,{exact:true}).waitFor()
 }
 assert.deepEqual(requests,[]);assert.deepEqual(errors,[])
 console.log('Current spawn/followup ToolBlock states and relative times: en/zh/en, themes, widths, original tool names/output and no requests passed')
},{entry:'/__tool-activity-entry.js',vite:{plugins:[{name:'tool-activity-fixture',resolveId(id){if(id==='/__tool-activity-entry.js')return id},load(id){if(id==='/__tool-activity-entry.js')return entry},configureServer(server){server.middlewares.use('/__tool-activity',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__tool-activity','<html><body><div id="root"></div><script type="module" src="/__tool-activity-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
