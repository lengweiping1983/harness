import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {TeamTemplates} from '/src/components/overlays/TeamTemplates.tsx'
import {AgentDefinitionCreator} from '/src/components/overlays/AgentDefinitionCreator.tsx'
import {setLocale} from '/src/i18n/index.ts'
import {useAppStore} from '/src/store/app.ts'
import '/src/styles.css'
await setLocale('en');window.locale=setLocale;window.choices=[];window.refreshed=0
useAppStore.setState({activeSessionId:null})
function App(){const [seed,setSeed]=useState(null);return React.createElement(React.Fragment,null,React.createElement(TeamTemplates,{onChoose:team=>{window.choices.push(team);setSeed(team)}}),React.createElement(AgentDefinitionCreator,{sessionId:null,seed,agents:[],refresh:async()=>{window.refreshed++},initialKind:'team'}))}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],writes=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(request.method()==='POST'){
   assert.equal(path,'/api/v1/plugins/agents/create');writes.push(request.postDataJSON())
   return route.fulfill({json:{created:true,agent:request.postDataJSON()}})
  }
  if(path==='/api/v1/personalities')return route.fulfill({json:{profiles:[]}})
  if(path==='/api/v1/external-agents')return route.fulfill({json:{runners:[],delegation_enabled:true}})
  return route.fulfill({json:{}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__team-templates')
 const ids=['delivery','security','product','performance','research']
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const t=key=>resources[locale][key]
  await page.getByText(t('ui.teamTemplates.title'),{exact:true}).waitFor()
  for(const id of ids){
   const name=t('ui.teamTemplates.'+id+'.name')
   await page.getByRole('button',{name:t('ui.teamTemplates.chooseNamed').replace('{name}',name),exact:true}).click()
   const choice=await page.evaluate(()=>window.choices.at(-1))
   assert.equal(choice.display_name,name);assert.equal(choice.model,null)
   assert.equal(choice.members.length,4);assert.equal(choice.members[0].access,'read')
   for(const member of choice.members){assert.equal(member.execution,'model');assert.ok(member.prompt);assert.equal(member.agent,undefined);assert.equal(member.model,undefined)}
   assert.deepEqual(choice.members.filter(row=>row.access==='inherit').map(row=>row.name),id==='delivery'?['builder']:[])
   if(id==='delivery'){
    assert.deepEqual(choice.members[2].depends_on,['planner']);assert.deepEqual(choice.members[3].depends_on,['builder'])
   }
   const dialog=page.getByRole('dialog').last()
   await dialog.getByRole('textbox',{name:t('ui.agent.author.teamName'),exact:true}).waitFor()
   assert.equal(await dialog.getByRole('textbox',{name:t('ui.agent.author.teamName'),exact:true}).inputValue(),name)
   const prompt=dialog.getByRole('textbox',{name:t('ui.agent.author.prompt'),exact:true})
   assert.equal(await prompt.inputValue(),choice.system_prompt)
   await prompt.fill(choice.system_prompt+'\nUser original 原文')
   // 同一草稿的语言变化只更新界面，不能重建指令或触发写入。
   const other=locale==='en'?'zh-CN':'en'
   await page.evaluate(async value=>window.locale(value),other)
   assert.equal(await dialog.getByRole('textbox',{name:resources[other]['ui.agent.author.prompt'],exact:true}).inputValue(),choice.system_prompt+'\nUser original 原文')
   assert.equal(writes.length,0)
   await page.evaluate(async value=>window.locale(value),locale)
   await dialog.getByRole('button',{name:t('ui.agent.author.cancel'),exact:true}).click()
   // 关闭草稿后清除测试自己的草稿；不接触用户浏览器。
   await page.evaluate(()=>sessionStorage.clear())
  }
 }
 // 保存实际编辑器生成的交付配置，验证权限与依赖未被表单丢弃。
 const t=key=>resources.en[key]
 await page.getByRole('button',{name:t('ui.teamTemplates.chooseNamed').replace('{name}',t('ui.teamTemplates.delivery.name')),exact:true}).click()
 await page.getByRole('dialog').last().getByRole('button',{name:t('ui.agent.author.createOnly'),exact:true}).click()
 await page.waitForFunction(()=>window.refreshed===1)
 assert.equal(writes.length,1);assert.equal(writes[0].scope,'user');assert.equal(writes[0].model,'')
 assert.deepEqual(writes[0].members.map(row=>row.access),['read','read','inherit','read'])
 assert.deepEqual(writes[0].members[2].depends_on,['planner']);assert.deepEqual(writes[0].members[3].depends_on,['builder'])
 assert.deepEqual(errors,[])
 await page.close()
},{entry:'/__team-templates-entry.js',vite:{plugins:[{name:'team-templates',resolveId(id){if(id==='/__team-templates-entry.js')return id},load(id){if(id==='/__team-templates-entry.js')return entry},configureServer(server){server.middlewares.use('/__team-templates',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__team-templates','<html><body><div id="root"></div><script type="module" src="/__team-templates-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
