import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const statuses=['pending','running','done','failed','blocked','interrupted','completed','error','constructor']
const roles=['lead','member','Original role 原文']
const sources=['pending','preparing','ready','failed']
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TeamBlock} from '/src/components/chat/blocks/MessageBlocks.tsx'
import {readTeamProgress} from '/src/lib/teamProgress.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale
const members=${JSON.stringify(statuses)}.flatMap(status=>${JSON.stringify(roles)}.map((role,index)=>({name:status+'-'+index,display_name:'Original '+status+' '+index+' 原文',role,status})))
const worktrees=${JSON.stringify(sources)}.map(status=>({id:status,path:'/Original/'+status,team_member:'Original '+status+' 原文',branch:'Original branch '+status,source_snapshot:{tree:'a'.repeat(40),status}}))
const block=readTeamProgress({run_id:'fixture',phase:'execute',team:'Original team 原文',members,worktrees})
window.snapshot=()=>JSON.stringify(block)
createRoot(document.getElementById('root')).render(React.createElement(TeamBlock,{block}))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[];page.setDefaultTimeout(10000)
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
 await page.goto(server.resolvedUrls.local[0]+'__team-status-resource')
 await page.getByRole('button',{name:'Original team 原文',exact:true}).click()
 const snapshot=await page.evaluate(()=>window.snapshot())
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  for(const status of statuses)for(const [index,role] of roles.entries()){
   const name='Original '+status+' '+index+' 原文'
   const roleText=index<2?resources[locale]['ui.team.member.role.'+role]:role
   const title=resources[locale]['ui.team.member.roleTitle'].replace('{name}',name).replace('{role}',roleText)
   const member=page.getByTitle(title,{exact:true});await member.waitFor()
   await member.getByText(resources[locale]['ui.member.status.'+(statuses.indexOf(status)<6?status:'unknown')],{exact:true}).waitFor()
  }
  for(const source of sources){
   const section=page.locator('section').filter({has:page.getByText('/Original/'+source,{exact:true})})
   await section.getByText('Original branch '+source,{exact:true}).waitFor()
   await section.getByText(resources[locale]['ui.team.worktrees.sourceVersion']+' '+ 'a'.repeat(12)+' · '+resources[locale]['ui.team.worktrees.sourceStatus.'+source],{exact:true}).waitFor()
  }
  assert.equal(await page.evaluate(()=>window.snapshot()),snapshot)
  assert.deepEqual(requests,[])
 }
 assert.deepEqual(errors,[])
},{entry:'/__team-status-resource-entry.js',vite:{plugins:[{name:'team-status-resource',resolveId(id){if(id==='/__team-status-resource-entry.js')return id},load(id){if(id==='/__team-status-resource-entry.js')return entry},configureServer(server){server.middlewares.use('/__team-status-resource',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__team-status-resource','<html><body><div id="root"></div><script type="module" src="/__team-status-resource-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
