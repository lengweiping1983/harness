import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TeamMemberPicker} from '/src/components/overlays/TeamMemberPicker.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.actions=[]
const agents=[{name:'raw',display_name:'Original name 原文',description:'Original description 原文',agent_type:'agent',enabled:true},{name:'hidden',description:'Hidden',agent_type:'team',enabled:true}]
createRoot(document.getElementById('root')).render(React.createElement('main',null,...['normal','selected','invalid','limit','empty'].map(mode=>React.createElement('div',{'data-mode':mode,key:mode},React.createElement(TeamMemberPicker,{agents:mode==='empty'?[]:agents,selected:mode==='selected'?['raw']:[],count:mode==='limit'?8:0,valid:mode!=='invalid',onAdd:name=>window.actions.push(name),onAddExternal:name=>window.actions.push(name)})))))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__team-picker-resource')
 for(const mode of ['normal','selected','invalid','limit','empty'])await page.locator('[data-mode='+mode+'] summary').click()
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const text=key=>{const value=resources[locale]['ui.agent.author.'+key];assert.ok(value,key);return value}
  for(const mode of ['normal','selected','invalid','limit','empty']){
   const scope=page.locator('[data-mode='+mode+']');await scope.getByRole('region',{name:text('availableAgents'),exact:true}).waitFor()
   const search=scope.getByRole('textbox',{name:text('memberSearch'),exact:true});assert.equal(await search.getAttribute('placeholder'),text('memberSearch'))
   for(const key of ['externalMembers','externalMembersHelp'])await scope.getByText(text(key),{exact:true}).waitFor()
   for(const runner of ['Codex','Claude Code','OpenCode'])await scope.getByRole('button',{name:text('add')+' · '+runner,exact:true}).waitFor()
   if(mode==='invalid')await scope.getByRole('alert').getByText(text('memberInvalid'),{exact:true}).waitFor()
   if(mode==='limit')await scope.getByText(text('memberLimit'),{exact:true}).waitFor()
   if(mode==='selected')await scope.getByText(text('memberAdded'),{exact:true}).waitFor({state:'attached'})
   if(mode==='empty')await scope.getByText(text('memberEmpty'),{exact:true}).waitFor()
   else{
    const button=scope.getByRole('button',{name:text('add')+' · Original name 原文 · raw',exact:true});assert.equal(await button.isDisabled(),mode!=='normal')
    await search.fill('no match');await scope.getByText(text('memberEmpty'),{exact:true}).waitFor();await search.fill('')
   }
  }
  assert.deepEqual(await page.evaluate(()=>window.actions),[])
 }
 assert.deepEqual(errors,[]);await page.close()
},{entry:'/__team-picker-resource-entry.js',vite:{plugins:[{name:'team-picker-resource',resolveId(id){if(id==='/__team-picker-resource-entry.js')return id},load(id){if(id==='/__team-picker-resource-entry.js')return entry},configureServer(server){server.middlewares.use('/__team-picker-resource',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__team-picker-resource','<html><body><div id="root"></div><script type="module" src="/__team-picker-resource-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
