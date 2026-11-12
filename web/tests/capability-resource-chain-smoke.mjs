import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const resources=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React,{useRef,useState} from 'react'
import {createRoot} from 'react-dom/client'
import {CapabilityPicker} from '/src/components/chat/CapabilityPicker.tsx'
import {CatalogToolbar} from '/src/components/overlays/ExtensionCatalogControls.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useChatStore,emptySession} from '/src/store/chat.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.actions=[]
const kind=new URLSearchParams(location.search).get('kind')
useAppStore.setState({activeSessionId:'A'})
useChatStore.setState({sessions:{A:{...emptySession(),agentMount:{revision:1,available:true,agent:'raw-agent',agent_type:'agent',display_name:'Original Agent 原文'}}}})
function Fixture(){
 const anchor=useRef(null),[open,setOpen]=useState(false),[draft,setDraft]=useState(''),[scope,setScope]=useState('project'),[query,setQuery]=useState('Original query 原文 {query}')
 window.selectSkill=()=>setDraft('/skill raw-skill Original task 原文')
 window.snapshot=()=>({draft,scope,query,mount:useChatStore.getState().sessions.A.agentMount})
 return React.createElement('main',{className:'space-y-4 p-3'},
  React.createElement('section',{'data-toolbar':true},React.createElement(CatalogToolbar,{query,onQuery:value=>{window.actions.push(['query',value]);setQuery(value)},scope,onScope:value=>{window.actions.push(['scope',value]);setScope(value)},count:1,total:3,label:'Original search 原文'})),
  React.createElement('button',{ref:anchor,onClick:()=>setOpen(true)},'Open fixture'),
  React.createElement(CapabilityPicker,{capabilityKind:kind,sessionId:'A',draft,onChange:value=>{window.actions.push(['draft',value]);setDraft(value)},focusEditor:()=>window.actions.push(['focus']),open,setOpen,menuAnchor:anchor}))
}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
await browserScenario(async({server,browser})=>{
 for(const kind of ['agents','skills']){
  const page=await browser.newPage(),errors=[],writes=[]
  page.on('pageerror',error=>errors.push(error.message))
  let release,entered,held,mode='catalog',reads=0
  const prepare=()=>{held=new Promise(resolve=>entered=resolve);return new Promise(resolve=>release=resolve)}
  let gate=prepare()
  const agents=[{name:'raw-agent',display_name:'Original Agent 原文',agent_type:'agent',description:'Original description 原文',enabled:true},{name:'raw-team',display_name:'Original Team 原文',agent_type:'team',description:'Original team description',enabled:true}]
  const skills=[{name:'raw-skill',description:'Original description 原文',enabled:true,interface:{display_name:'Original Skill 原文',short_description:'Original short description 原文'}}]
  await page.route('**/api/v1/**',async route=>{
   if(route.request().method()!=='GET'){writes.push(route.request().url());return route.fulfill({status:500})}
   if(route.request().url().includes('view=picker')){
    reads++;const current=gate;entered?.();await current
    return route.fulfill(mode==='error'?{status:500,json:{message:'Original catalog error 原文 {error}'}}:{json:{agents,skills,capabilities:{persistent_team_selection:true}}})
   }
   return route.fulfill({status:204})
  })
  await page.goto(server.resolvedUrls.local[0]+'__capability-resource-chain?kind='+kind)
  await page.getByRole('button',{name:'Open fixture',exact:true}).click()
  const prefix=kind==='agents'?'ui.composerAgents':'ui.composerSkills'
  let currentLocale='en'
  const copy=async key=>{
   assert.ok(Object.hasOwn(resources[currentLocale],key),'Missing browser resource: '+currentLocale+' '+key)
   assert.ok(resources[currentLocale][key].trim())
   const value=await page.evaluate(key=>window.copy(key),key)
   assert.equal(value,resources[currentLocale][key]);return value
  }
  const toolbar=page.locator('[data-toolbar]')
  for(const locale of ['en','zh-CN','en']){
   currentLocale=locale;await page.evaluate(locale=>window.locale(locale),locale);await held
   const dialog=page.getByRole('dialog',{name:await copy(prefix+'.choose'),exact:true})
   await dialog.waitFor()
   await dialog.getByRole('status').getByText(await copy(prefix+'.loading'),{exact:true}).waitFor()
   await dialog.getByRole('button',{name:await copy(prefix+'.close'),exact:true}).waitFor()
   const search=dialog.getByRole('combobox',{name:await copy(prefix+'.search'),exact:true})
   assert.equal(await search.getAttribute('placeholder'),await copy(prefix+'.search'))
   await dialog.getByText(await copy(prefix+'.hint'),{exact:true}).waitFor()
   release();await dialog.getByRole('option').first().waitFor()
   for(const dark of [false,true])for(const width of [390,1440]){
    await page.setViewportSize({width,height:900});await page.evaluate(dark=>document.documentElement.classList.toggle('dark',dark),dark)
    const scopeSelect=toolbar.getByRole('combobox',{name:await copy('ui.esc.search.scope'),exact:true})
    await scopeSelect.waitFor()
    assert.equal(await scopeSelect.inputValue(),'project')
    for(const scope of ['all','project','shared','bundled'])assert.equal(await scopeSelect.locator('option[value='+scope+']').textContent(),await copy('ui.esc.search.'+scope))
    await toolbar.getByRole('button',{name:await copy('ui.esc.search.clear'),exact:true}).waitFor()
    await toolbar.getByRole('status').getByText(await page.evaluate(()=>window.copy('ui.esc.search.count',{count:1,total:3})),{exact:true}).waitFor()
    assert.equal(await toolbar.getByRole('searchbox',{name:'Original search 原文',exact:true}).inputValue(),'Original query 原文 {query}')
    await dialog.getByRole('listbox',{name:await copy(prefix+'.choose'),exact:true}).waitFor()
    if(kind==='agents'){
     for(const value of ['all','agent','team'])await dialog.getByRole('button',{name:await copy('ui.composerAgents.'+value),exact:true}).waitFor()
     await dialog.getByRole('button',{name:await copy('ui.composerAgents.team'),exact:true}).click()
     assert.deepEqual(await dialog.getByRole('option').allTextContents(),['Original Team 原文raw-teamOriginal team description'])
     await dialog.getByRole('button',{name:await copy('ui.composerAgents.all'),exact:true}).click()
    }
    await search.fill('no-match-原文');await dialog.getByRole('status').getByText(await copy(prefix+'.empty'),{exact:true}).waitFor()
    await search.fill('')
    await dialog.getByRole('option').getByText(kind==='agents'?'Original Agent 原文':'Original Skill 原文',{exact:true}).waitFor()
    await dialog.getByRole('option').getByText(kind==='agents'?'Original description 原文':'Original short description 原文',{exact:true}).waitFor()
   }
   gate=prepare()
  }
  // 错误仍按原文呈现；语言切换仅重新读取目录。
  mode='error'
  for(const locale of ['zh-CN','en']){
   currentLocale=locale;await page.evaluate(locale=>window.locale(locale),locale);await held;release()
   const dialog=page.getByRole('dialog',{name:await copy(prefix+'.choose'),exact:true})
   await dialog.getByRole('alert').getByText('Original catalog error 原文 {error}',{exact:true}).waitFor()
   await dialog.getByRole('button',{name:await copy('ui.composerAgents.retry'),exact:true}).waitFor()
   gate=prepare()
  }
  mode='catalog';gate=Promise.resolve();entered=null
  await page.getByRole('dialog').getByRole('button',{name:await copy(prefix+'.close'),exact:true}).click()
  if(kind==='skills')await page.evaluate(()=>window.selectSkill())
  for(const locale of ['en','zh-CN','en']){
   currentLocale=locale;await page.evaluate(locale=>window.locale(locale),locale)
   await page.getByRole('button',{name:await copy(prefix+'.choose'),exact:true}).waitFor()
   await page.getByRole('button',{name:await copy(kind==='skills'?prefix+'.remove':'ui.composerAgents.unmount'),exact:true}).waitFor()
  }
  const snapshot=await page.evaluate(()=>window.snapshot())
  assert.equal(snapshot.draft,kind==='skills'?'/skill raw-skill Original task 原文':'')
  assert.equal(snapshot.mount.agent,'raw-agent');assert.equal(snapshot.scope,'project');assert.equal(snapshot.query,'Original query 原文 {query}')
  assert.equal(reads,kind==='skills'?8:5)
  assert.deepEqual(writes,[]);assert.deepEqual(await page.evaluate(()=>window.actions),[]);assert.deepEqual(errors,[])
  await page.close()
 }
 console.log('Capability resources: both picker prefixes and catalog scope labels switch en→zh→en with loading/empty/error, raw catalog data, selection and write isolation')
},{entry:'/__capability-resource-chain-entry.js',vite:{plugins:[{name:'capability-resource-chain',resolveId(id){if(id==='/__capability-resource-chain-entry.js')return id},load(id){if(id==='/__capability-resource-chain-entry.js')return entry},configureServer(server){server.middlewares.use('/__capability-resource-chain',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__capability-resource-chain','<html><body><div id="root"></div><script type="module" src="/__capability-resource-chain-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
