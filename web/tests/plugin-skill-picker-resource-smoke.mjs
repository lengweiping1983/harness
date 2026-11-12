import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {readFileSync} from 'node:fs'
import {browserScenario} from './browser-scenario.mjs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {AgentSkillPicker} from '/src/components/overlays/AgentSkillPicker.tsx'
import {PluginAppReferences} from '/src/components/overlays/PluginsPage.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.changes=[]
function Picker({mode}){const [value,setValue]=useState(mode==='inherited'?null:mode==='selected'?['raw']:mode==='missing'?['ghost']:[]);return React.createElement('section',{'data-picker':mode},React.createElement(AgentSkillPicker,{sessionId:mode,external:mode==='external',value,onChange:value=>{window.changes.push([mode,value]);setValue(value)}}))}
createRoot(document.getElementById('root')).render(React.createElement('main',null,
 React.createElement(PluginAppReferences,{item:{apps:['ready','not_connected','unavailable',undefined].map((status,index)=>({name:'Original App '+index,id:'Original-ID-'+index,status,connected:status==='ready',candidates:['Original connector'],connectors:[]}))},busy:false,onLink:()=>window.changes.push('link')}),
 ...['external','inherited','selected','missing','empty','disabled','error','loading'].map(mode=>React.createElement(Picker,{mode,key:mode}))))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));let release
 const held=new Promise(resolve=>release=resolve),requests=[]
 await page.route('**/api/v1/**',async route=>{
  const req=route.request();assert.equal(req.method(),'GET');const mode=new URL(req.url()).searchParams.get('session_id');requests.push(mode)
  if(mode==='loading')await held
  return route.fulfill(mode==='error'?{status:503,json:{code:'fixture/error',message:'Original catalog error 原文'}}:{json:{enabled:mode!=='disabled',skills:mode==='empty'?[]:[{name:'raw',description:'Original description 原文',enabled:true,plugin_enabled:true,interface:{display_name:'Original skill 原文',short_description:'Original short 原文'}},{name:'blocked',description:'Original blocked 原文',enabled:false,plugin_enabled:false}]}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__plugin-skill-picker-resource')
 for(const mode of ['inherited','selected','missing','empty','disabled','error','loading'])await page.locator('[data-picker='+mode+'] summary').click()
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  const text=key=>{assert.ok(resources[locale][key],key);return resources[locale][key]}
  for(const [index,status] of ['ready','not_connected','unavailable','unavailable'].entries()){
   const card=page.getByText('Original-ID-'+index,{exact:true}).locator('..');await card.getByText(text('ui.extensions.appStatus.'+status),{exact:true}).waitFor();assert.equal(await card.locator('select').inputValue(),'Original connector')
  }
  for(const [mode,key] of [['external','externalHint'],['inherited','inherited'],['empty','empty'],['disabled','disabled'],['error','retry'],['loading','loading']])await page.locator('[data-picker='+mode+']').getByText(text('ui.agent.skills.'+key),{exact:true}).waitFor()
  await page.locator('[data-picker=error]').getByText('Original catalog error 原文',{exact:true}).waitFor()
  for(const mode of ['inherited','selected','missing','empty']){
   const picker=page.locator('[data-picker='+mode+']')
   for(const key of ['hint','inherit','selectVisible','clear'])await picker.getByText(text('ui.agent.skills.'+key),{exact:true}).waitFor()
   const search=picker.getByRole('searchbox',{name:text('ui.agent.skills.search'),exact:true});await search.waitFor()
   if(mode!=='empty'){
    await picker.getByText('Original skill 原文',{exact:true}).waitFor();await picker.getByText('Original short 原文',{exact:true}).waitFor();await picker.getByText(text('ui.agent.skills.pluginDisabled'),{exact:true}).waitFor()
    await search.fill('no-match');await picker.getByText(text('ui.agent.skills.noMatches'),{exact:true}).waitFor();await search.fill('')
   }
   if(mode==='missing')await picker.getByText('ghost · '+text('ui.agent.skills.missing'),{exact:true}).waitFor()
   if(mode==='empty')await picker.getByText(text('ui.agent.skills.noneHint'),{exact:true}).waitFor()
   if(mode==='selected')assert.equal(await picker.getByRole('switch',{name:'raw',exact:true}).isChecked(),true)
  }
  assert.deepEqual(await page.evaluate(()=>window.changes),[])
 }
 release();assert.ok(!requests.includes('external'));assert.deepEqual(errors,[]);await page.close();console.log('Plugin app statuses and skill picker labels: all finite states, original catalog/selection and en→zh→en without writes')
},{entry:'/__plugin-skill-picker-resource-entry.js',vite:{plugins:[{name:'plugin-skill-picker-resource',enforce:'pre',resolveId(id){if(id==='/__plugin-skill-picker-resource-entry.js')return id},async load(id){if(id.endsWith('/PluginsPage.tsx'))return await readFile(id,'utf8')+'\nexport {PluginAppReferences}\n';if(id==='/__plugin-skill-picker-resource-entry.js')return entry},configureServer(server){server.middlewares.use('/__plugin-skill-picker-resource',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__plugin-skill-picker-resource','<html><body><div id="root"></div><script type="module" src="/__plugin-skill-picker-resource-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
