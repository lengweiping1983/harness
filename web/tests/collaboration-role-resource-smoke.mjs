import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {execFileSync} from 'node:child_process'
import {fixturePython} from './python-fixture.mjs'
import {browserScenario} from './browser-scenario.mjs'
const roles=JSON.parse(execFileSync(fixturePython,['-c',"import json;from harness.web.protocol import SCHEMAS;print(json.dumps(SCHEMAS['InvitationCreated']['properties']['role']['enum']))"],{cwd:new URL('../../',import.meta.url),encoding:'utf8'}))
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {CollaborationButton} from '/src/components/shared/CollaborationButton.tsx'
import {collaboration} from '/src/lib/collaboration.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.reads=0
const roles=${JSON.stringify(roles)}
const caps={observer:['read'],collaborator:['read','contribute'],administrator:['read','contribute','manage']}
window.state={origin:'https://fixture.invalid',access:{session_id:'owner',owner:'local-owner',revision:7,members:{'local-owner':['manage'],...Object.fromEntries(roles.map(role=>['Original-'+role,caps[role]]))},profiles:Object.fromEntries(roles.map(role=>['Original-'+role,{display_name:'Original profile 原文 '+role}]))},invitations:roles.map(role=>({id:'Original invite '+role,display_name:'Original invitation 原文 '+role,role,status:'open',expires_at:123}))}
collaboration.state=async()=>{window.reads++;return {ok:true,data:window.state}}
function Fixture(){const [open,setOpen]=React.useState(false);return React.createElement(CollaborationButton,{sessionId:'owner',open,onOpen:()=>setOpen(true),onClose:()=>setOpen(false)})}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().method());return route.fulfill({status:500})})
 await page.goto(server.resolvedUrls.local[0]+'__collaboration-role-resource')
 await page.getByRole('button',{name:resources.en['ui.collaboration.invite'],exact:true}).click()
 const original=await page.evaluate(()=>window.state)
 let selected='collaborator'
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const dialog=page.getByRole('dialog',{name:resources[locale]['ui.collaboration.title'],exact:true});await dialog.waitFor()
  const invite=dialog.getByRole('combobox',{name:resources[locale]['ui.collaboration.invitationRole'],exact:true});assert.equal(await invite.inputValue(),selected)
  for(const role of roles){
   const label=resources[locale]['ui.collaboration.'+role];assert.ok(label)
   assert.equal(await invite.locator('option[value='+role+']').textContent(),label)
   await invite.selectOption(role);selected=role
   await dialog.getByText('Original invitation 原文 '+role+' · '+label,{exact:true}).waitFor()
   const memberLabel=resources[locale]['ui.collaboration.memberRole'].replace('{name}','Original profile 原文 '+role)
   const member=dialog.getByRole('combobox',{name:memberLabel,exact:true});assert.equal(await member.inputValue(),role)
   for(const value of roles)assert.equal(await member.locator('option[value='+value+']').textContent(),resources[locale]['ui.collaboration.'+value])
  }
  assert.deepEqual(await page.evaluate(()=>window.state),original);assert.equal(await page.evaluate(()=>window.reads),1);assert.deepEqual(requests,[])
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Current invitation roles, saved invitation labels and member role options translate without access mutations or repeated reads')
},{entry:'/__collaboration-role-resource-entry.js',vite:{plugins:[{name:'collaboration-role-resource',resolveId(id){if(id==='/__collaboration-role-resource-entry.js')return id},load(id){if(id==='/__collaboration-role-resource-entry.js')return entry},configureServer(server){server.middlewares.use('/__collaboration-role-resource',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__collaboration-role-resource','<html><body><div id="root"></div><script type="module" src="/__collaboration-role-resource-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
