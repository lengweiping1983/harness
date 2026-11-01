import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ReadinessCheck} from '/src/components/chat/ReadinessCheck.tsx'
import {api} from '/src/lib/api.ts'
import {useAppStore} from '/src/store/app.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.creations=[]
useDraftStore.setState({bySession:{},submissions:{}})
useAppStore.setState({activeSessionId:null,activeWorkspaceId:'B',newSessionRevision:1,workspaces:[{id:'B',path:'/project/B',name:'Project 原文',color:'blue'},{id:'C',path:'/project/C',name:'Other',color:'teal'}],workspace:{root:'/host/A'},status:null,sessions:[{id:'existing',cwd:'/legacy',resolved_cwd:'/actual/existing',model:'fixture'}]})
useAppStore.getState().setNewSessionCheckout({cwd:'/project/B',mode:'local',expected_head:null,expected_branch:null})
api.createSession=async(body,id)=>{window.creations.push(body);return {ok:true,data:{session:{id:'created',cwd:body.cwd,metadata:{creation_request_id:id,creation_completed:true}}}}}
window.choose=id=>useAppStore.getState().startNewSession(id)
window.existing=()=>useAppStore.setState({activeSessionId:'existing'})
window.configureStatus=value=>useAppStore.setState({status:value===null?null:{config:{has_api_key:value}}})
window.create=()=>useAppStore.getState().materializeSession()
window.state=()=>{const s=useAppStore.getState();return {settingsOpen:s.settingsOpen,settingsTarget:s.settingsTarget}}
createRoot(document.getElementById('root')).render(<ReadinessCheck/>)
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(), errors=[], requests=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:503,json:{error:'Unexpected fixture request'}})})
 await page.goto(server.resolvedUrls.local[0]+'__readiness-scope')
 const copy=(key,params)=>page.evaluate(({key,params})=>window.copy(key,params),{key,params})
 const directory=async value=>page.getByText(await copy('ui.readiness.project',{directory:value}),{exact:true}).waitFor()
 await directory('/project/B')
 for(const value of [null,false,true]){
  await page.evaluate(value=>window.configureStatus(value),value)
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440])for(const font of [10,22]){
   await page.setViewportSize({width,height:900})
   await page.evaluate(({locale,dark,font})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark);document.documentElement.style.fontSize=font+'px'},{locale,dark,font})
   await directory('/project/B')
   await page.getByText(await copy(value===null?'ui.readiness.modelUnknown':value?'ui.readiness.model':'ui.readiness.noModel'),{exact:true}).waitFor()
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  }
 }
 await page.getByRole('button',{name:await copy('ui.readiness.openModels'),exact:true}).click()
 assert.deepEqual(await page.evaluate(()=>window.state()),{settingsOpen:true,settingsTarget:'models'})
 assert.equal(await page.evaluate(()=>window.create()),true)
 await directory('/project/B')
 assert.deepEqual(await page.evaluate(()=>window.creations),[{cwd:'/project/B',project:{workspace_id:'B',mode:'local',expected_head:null,expected_branch:null}}],'The shown target and checked project must equal the actual session creation request')
 await page.getByRole('button',{name:await copy('ui.readiness.dismiss'),exact:true}).focus()
 await page.keyboard.press('Enter')
 assert.equal(await page.getByRole('region',{name:await copy('ui.readiness.title'),exact:true}).count(),0)
 await page.evaluate(()=>window.choose('C'));await directory('/project/C')
 await page.evaluate(()=>window.existing());await directory('/actual/existing')
 await page.evaluate(()=>window.choose(null))
 await page.getByText(await copy('ui.readiness.noProject'),{exact:true}).waitFor()
 assert.equal(await page.evaluate(()=>window.create()),false,'No project must not fall back to the host directory')
 assert.equal(await page.evaluate(()=>window.creations.length),1)
 await page.evaluate(()=>window.choose('B'))
 assert.equal(await page.getByRole('region',{name:await copy('ui.readiness.title'),exact:true}).count(),0,'Dismissal belongs to the actual target directory')
 assert.deepEqual(requests,[]);assert.deepEqual(errors,[])
 await page.close()
 console.log('Readiness: selected project and resolved session directory match actual creation; unknown/missing/provided key states, keyboard dismissal and locale/theme/width/font matrix passed without probes')
},{entry:'/__readiness-scope-entry.jsx',vite:{plugins:[{
 name:'readiness-scope-fixture',resolveId(id){if(id==='/__readiness-scope-entry.jsx')return id},load(id){if(id==='/__readiness-scope-entry.jsx')return entry},
 configureServer(server){server.middlewares.use('/__readiness-scope',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__readiness-scope','<html><body><div id="root"></div><script type="module" src="/__readiness-scope-entry.jsx"></script></body></html>'))}catch(error){next(error)}})},
}]}})
