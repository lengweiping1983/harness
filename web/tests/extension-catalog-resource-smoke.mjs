import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {readFile as readAsync} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AgentsTab,SkillsTab} from '/src/components/overlays/EscPanel.tsx'
import {teamReferenceIssues} from '/src/lib/teamReferences.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.actions=[]
const noop=()=>{window.actions.push('action');return Promise.resolve(false)}
const empty={agents:[],skills:[],connectors:[],enabled:{agents:true,skills:true,connectors:false}}
const members=[{name:'Original missing 原文',agent:'missing',role:'member'},{name:'Original disabled 原文',agent:'disabled',role:'member'},{name:'Original nested 原文',agent:'nested',role:'member'}]
const agents=[{name:'Original team 原文',agent_type:'team',enabled:true,description:'Original description 原文',members},{name:'disabled',agent_type:'agent',enabled:false,description:'Original disabled'},{name:'nested',agent_type:'team',enabled:true,description:'Original nested',members:[]}]
window.issues=teamReferenceIssues(members,agents)
const sources=['project:/fixture','bundled','system-harness','user','Original source']
createRoot(document.getElementById('root')).render(React.createElement('main',null,
 ...['agent','team'].map(kind=>React.createElement('section',{'data-empty':kind,key:kind},React.createElement(AgentsTab,{initialKind:kind,overview:empty,mount:null,sessionId:null,busy:'',perform:noop,refresh:noop}))),
 React.createElement('section',{'data-issues':true},React.createElement(AgentsTab,{initialKind:'team',overview:{...empty,agents},mount:null,sessionId:null,busy:'',perform:noop,refresh:noop})),
 React.createElement('section',{'data-skills':true},React.createElement(SkillsTab,{overview:{...empty,skills:sources.map((source,index)=>({name:'Original skill '+index,description:'Original description 原文',source,enabled:true}))},sessionId:null,busy:'',perform:noop}))))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__extension-catalog-resource')
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const text=key=>{assert.ok(resources[locale][key]);return resources[locale][key]}
  for(const kind of ['agent','team']){
   const section=page.locator('[data-empty='+kind+']'),search=section.getByRole('searchbox',{name:text('ui.esc.search.agents'),exact:true})
   await section.getByText(text('ui.agent.catalog.empty.all.'+kind),{exact:true}).waitFor();await section.getByText(text('ui.agent.catalog.createHint.'+kind),{exact:true}).waitFor()
   await search.fill('Original no match');await section.getByText(text('ui.esc.search.empty'),{exact:true}).waitFor();await section.getByText(text('ui.esc.search.emptyHint'),{exact:true}).waitFor()
   await section.getByRole('button',{name:text('ui.agent.catalog.showAll.'+kind),exact:true}).click();assert.equal(await search.inputValue(),'')
  }
  for(const issue of await page.evaluate(()=>window.issues)){
   const expected=text('ui.agent.team.reference.'+issue.reason).replace('{member}',issue.member).replace('{agent}',issue.agent)
   await page.locator('[data-issues]').getByText(expected,{exact:true}).waitFor()
  }
  const sourceKeys=['project','bundled','bundled','shared','shared']
  for(let index=0;index<sourceKeys.length;index++){
   const card=page.locator('[data-skills]').getByText('Original skill '+index,{exact:true}).locator('..').locator('..')
   await card.getByText(text('ui.esc.search.'+sourceKeys[index]),{exact:true}).waitFor()
  }
  assert.deepEqual(await page.evaluate(()=>window.actions),[])
 }
 assert.deepEqual(errors,[]);await page.close();console.log('Extension catalog empty agents/teams, current reference issues and skill source labels follow locale while original definitions remain unchanged')
},{entry:'/__extension-catalog-resource-entry.js',vite:{plugins:[{name:'extension-catalog-resource',enforce:'pre',resolveId(id){if(id==='/__extension-catalog-resource-entry.js')return id},async load(id){if(id.endsWith('/EscPanel.tsx'))return await readAsync(id,'utf8')+'\nexport {AgentsTab,SkillsTab}\n';if(id==='/__extension-catalog-resource-entry.js')return entry},configureServer(server){server.middlewares.use('/__extension-catalog-resource',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__extension-catalog-resource','<html><body><div id="root"></div><script type="module" src="/__extension-catalog-resource-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
