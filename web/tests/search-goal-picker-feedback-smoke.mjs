import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React,{useRef,useState} from 'react'
import {createRoot} from 'react-dom/client'
import {WebSearchSettings} from '/src/components/overlays/WebSearchSettings.tsx'
import {GoalEvidence} from '/src/components/chat/GoalEvidence.tsx'
import {CapabilityPicker} from '/src/components/chat/CapabilityPicker.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const view=new URLSearchParams(location.search).get('view')
const goal={id:'goal',phase:'complete',objective:'Objective 原文',criteria:['Criterion 原文'],completion:[{criterion:1,summary:'Summary 原文',evidence:[{sourceSeq:7,name:'FixtureTool',input:'Input 原文'}]}]}
function Picker(){const [open,setOpen]=useState(false),anchor=useRef(null);return React.createElement(React.Fragment,null,React.createElement('button',{ref:anchor,onClick:()=>setOpen(true)},'Open picker'),React.createElement(CapabilityPicker,{capabilityKind:'agents',sessionId:'owner',draft:'',onChange:()=>{},focusEditor:()=>{},open,setOpen,menuAnchor:anchor}))}
createRoot(document.getElementById('root')).render(view.startsWith('search')?React.createElement(WebSearchSettings):view==='evidence'?React.createElement(GoalEvidence,{sessionId:'owner',goal}):React.createElement(Picker))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['search-load','search-save','search-failure','evidence','picker-late','picker-external']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  const requests=[],errors=[];page.on('pageerror',error=>errors.push(error.message))
  let release,entered;const held=new Promise(resolve=>release=resolve),started=new Promise(resolve=>entered=resolve)
  const settings={deepseek_web_enabled:false,deepseek_web_api_key_configured:true,brave_web_enabled:false,brave_web_api_key_configured:true,deepseek_web_api_key_mask:'******3blr',brave_web_api_key_mask:'******6fln'}
  await page.route('**/api/v1/**',async route=>{
   const req=route.request(),path=new URL(req.url()).pathname;requests.push({method:req.method(),path,body:req.postDataJSON()})
   if(path.endsWith('/agents'))return route.fulfill({json:{agents:[{name:'FixtureAgent',description:'Description 原文',agent_type:'agent',enabled:true}],capabilities:{persistent_team_selection:true}}})
   if(req.method()==='POST'){
    if(view==='picker-late'){entered();await held}
    if(view==='picker-external')return route.fulfill({status:400,json:{code:'fixture/external',message:'Original refusal 原文'}})
    return route.fulfill({body:'broken',contentType:'application/json'})
   }
   if(view.startsWith('search')&&view!=='search-load'){
    if(req.method()==='PATCH'&&view==='search-failure')return route.fulfill({body:'broken',contentType:'application/json'})
    return route.fulfill({json:{settings}})
   }
   return route.fulfill({body:'broken',contentType:'application/json'})
  })
  await page.goto(server.resolvedUrls.local[0]+'__search-goal-picker?view='+view)
  if(view==='search-save'||view==='search-failure'){
   await page.getByLabel('DeepSeek API key',{exact:true}).fill('Private fixture 原文')
   await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.webSearch.save')),exact:true}).click()
  }
  if(view==='evidence')await page.getByRole('button').filter({hasText:'FixtureTool'}).click()
  if(view.startsWith('picker')){
   await page.getByRole('button',{name:'Open picker',exact:true}).click()
   await page.getByRole('option').filter({hasText:'FixtureAgent'}).click()
   if(view==='picker-late'){await started;await page.evaluate(()=>window.locale('zh-CN'));await page.getByRole('dialog',{name:await page.evaluate(()=>window.copy('ui.composerAgents.choose')),exact:true}).waitFor();release()}
  }
  const expected=()=>page.evaluate(view=>view==='search-save'?window.copy('ui.webSearch.saved'):view==='picker-external'?'Original refusal 原文':window.copy('ui.http.invalidResponse'),view)
  const check=async()=>{const text=await expected();if(view==='evidence')await page.getByRole('alert').filter({hasText:text}).waitFor();else await page.getByText(text,{exact:true}).first().waitFor()}
  await check();const count=requests.length
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await check();assert.equal(requests.length,count,'stored feedback must not trigger requests')
   if(view==='search-save'||view==='search-failure'){
    for(const name of ['deepseek','brave']){
     const label=await page.evaluate(name=>window.copy('ui.webSearch.'+name+'.enable'),name)
     assert.equal(await page.getByRole('switch',{name:label,exact:true}).isChecked(),false)
    }
    const keyLabel=await page.evaluate(()=>window.copy('ui.webSearch.apiKeyLabel',{provider:'DeepSeek'}))
    assert.equal(await page.getByLabel(keyLabel,{exact:true}).inputValue(),view==='search-save'?'':'Private fixture 原文')
    for(const mask of ['******3blr','******6fln']){
     const placeholder=await page.evaluate(mask=>window.copy('ui.components.overlays.ModelConnections.94fcf2ddcd2b',{p0:mask}),mask)
     assert.match(placeholder, /^\*{6}.{4}$/u)
     await page.getByPlaceholder(placeholder,{exact:true}).waitFor()
    }
   }
   if(view==='evidence')assert.ok((await page.locator('body').textContent()).includes('Input 原文'))
  }
  assert.equal(requests.filter(r=>r.method==='POST').length,view.startsWith('picker')?1:0)
  assert.equal(requests.filter(r=>r.method==='PATCH').length,['search-save','search-failure'].includes(view)?1:0)
  if(view.startsWith('picker'))assert.equal(requests.find(r=>r.method==='POST').body.session_id,'owner')
  assert.deepEqual(errors,[],view);await page.close();console.log(view+': current-language feedback, original content and single write verified')
 }
},{entry:'/__search-goal-picker-entry.js',vite:{plugins:[{
 name:'search-goal-picker-fixture',resolveId(id){if(id==='/__search-goal-picker-entry.js')return id},load(id){if(id==='/__search-goal-picker-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__search-goal-picker',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__search-goal-picker','<html><body><div id="root"></div><script type="module" src="/__search-goal-picker-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
