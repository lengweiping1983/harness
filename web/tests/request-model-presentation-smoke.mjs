import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ModelSeat} from '/src/components/chat/ModelPicker.tsx'
import {GenerationDock} from '/src/components/chat/GenerationDock.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useChatStore,useChat} from '/src/store/chat.ts'
import {connectionChanged} from '/src/lib/connectionRoutes.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.calls=[]
let routing={version:2,revision:'fixture',providers:[],models:[],current:null,cycle:1}
window.fetch=async(path,options={})=>{
 const method=options.method||'GET';window.calls.push({path,method})
 if(method==='PATCH'){await new Promise(resolve=>{window.refuse=resolve});return Response.json({message:'Original refusal'},{status:403})}
 if(path==='/api/v1/connection')return Response.json(routing)
 throw Error('Unexpected fixture request '+path)
}
useAppStore.setState({activeSessionId:'one',defaultModel:'selected/A',models:[{id:'selected/A',label:'Selection A',provider:'fixture'},{id:'selected/B',label:'Selection B',provider:'fixture'}],sessions:[{id:'one',model:'selected/A'},{id:'two',model:'selected/B'}]})
window.apply=(id,event)=>useChatStore.getState().apply(id,event)
window.select=id=>useAppStore.setState({activeSessionId:id})
window.choose=()=>useAppStore.getState().setSessionModel('one','selected/B')
window.chooseDefault=()=>useAppStore.getState().setDefaultModel('selected/B')
window.useDefault=()=>useAppStore.setState(s=>({sessions:s.sessions.map(session=>session.id==='one'?{...session,model:null}:session)}))
window.connection=value=>useChatStore.getState().setConnection('one',value)
window.route=model=>{routing={version:2,revision:'fixture',providers:[],models:[{id:'route',model,base_url:'https://example.invalid',enabled:true,keys:[{id:'key',mask:'******test',available:true,current:true,in_use:false}]}],current:model?{model_id:'route',model,key_id:'key'}:null,cycle:1};connectionChanged(routing)}
window.apply('one',{type:'request/start',request_id:'one-current',generation:{phase:'waiting',model:'request/A-original-very-long-model-name'}})
window.apply('two',{type:'request/start',request_id:'two-current',generation:{phase:'waiting',model:'request/B'}})
useChatStore.getState().setConnection('one','open');useChatStore.getState().setConnection('two','open')
function Fixture(){const owner=useAppStore(s=>s.activeSessionId),chat=useChat(owner);return <main className="mx-auto max-w-xl p-2"><ModelSeat/><GenerationDock generation={chat.generation} connection={chat.connection} stopping={false}/></main>}
createRoot(document.getElementById('root')).render(<Fixture/>)
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(), errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 await page.goto(server.resolvedUrls.local[0]+'__request-model-presentation')
 const copy=(key,params)=>page.evaluate(({key,params})=>window.copy(key,params),{key,params})
 const summary=page.locator('summary')
 const requestModel=async(model,key='ui.generation.requestModel')=>{
  const label=await copy(key,{model})
  await page.waitForFunction(label=>document.querySelector('summary')?.title===label,label)
  assert.equal(await page.locator('dd').first().textContent(),model,'Expanded details retain the actual request model, independently of the selected model')
 }
 await requestModel('request/A-original-very-long-model-name')
 assert.equal(await page.locator('details').getAttribute('open'),null,'Technical request details start folded; the summary retains the full model tooltip')
 await page.evaluate(()=>{window.pendingSave=window.choose()})
 await page.waitForFunction(()=>typeof window.refuse==='function')
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440])for(const font of [10,22]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,dark,font})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark);document.documentElement.style.fontSize=font+'px'},{locale,dark,font})
  await page.getByRole('button',{name:await copy('ui.models.selectionSaving',{p0:'Selection B'}),exact:true}).waitFor()
  await requestModel('request/A-original-very-long-model-name')
  assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.method==='PATCH').length),1)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }
 await page.evaluate(async()=>{window.refuse();await window.pendingSave})
 await page.getByRole('button',{name:await copy('ui.components.chat.ModelPicker.8b625429e269',{p0:'Selection A'}),exact:true}).waitFor()
 await requestModel('request/A-original-very-long-model-name')
 await page.setViewportSize({width:390,height:900})
 await summary.click()
 const fullModel=page.locator('dd').getByText('request/A-original-very-long-model-name',{exact:true})
 await fullModel.waitFor()
 assert.equal(await fullModel.evaluate(node=>getComputedStyle(node).wordBreak),'break-all','Touch users can expand the full model name')
 if(process.env.HARNESS_TEST_SCREENSHOT)await page.screenshot({path:process.env.HARNESS_TEST_SCREENSHOT})
 await summary.click()

 await page.evaluate(()=>{window.useDefault();window.refuse=null;window.pendingSave=window.chooseDefault()})
 await page.waitForFunction(()=>typeof window.refuse==='function')
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  await page.getByRole('button',{name:await copy('ui.models.selectionSaving',{p0:'Selection B'}),exact:true}).waitFor()
  await requestModel('request/A-original-very-long-model-name')
  await page.evaluate(()=>window.select('two'))
  await page.getByRole('button',{name:await copy('ui.components.chat.ModelPicker.8b625429e269',{p0:'Selection B'}),exact:true}).waitFor()
  await requestModel('request/B')
  await page.evaluate(()=>window.select(null))
  await page.getByRole('button',{name:await copy('ui.models.selectionSaving',{p0:'Selection B'}),exact:true}).waitFor()
  assert.equal(await summary.count(),0)
  await page.evaluate(()=>window.select('one'))
 }
 await page.evaluate(async()=>{window.refuse();await window.pendingSave})
 await page.getByRole('button',{name:await copy('ui.components.chat.ModelPicker.8b625429e269',{p0:'Selection A'}),exact:true}).waitFor()
 await requestModel('request/A-original-very-long-model-name')

 await page.evaluate(()=>window.route('candidate/C'))
 await page.getByRole('button',{name:/candidate\/C/}).waitFor()
 await requestModel('request/A-original-very-long-model-name')
 await page.evaluate(()=>window.apply('one',{type:'request/progress',request_id:'one-current',generation:{phase:'retrying',attempt:2,model:'request/C'}}))
 await requestModel('request/C')
 await page.evaluate(()=>{window.apply('one',{type:'request/progress',request_id:'one-old',generation:{model:'WRONG'}});window.apply('one',{type:'request/end',request_id:'one-old'})})
 await requestModel('request/C')
 await page.evaluate(()=>window.select('two'));await requestModel('request/B')
 await page.evaluate(()=>window.select('one'));await requestModel('request/C')
 await page.evaluate(()=>window.connection('closed'))
 await requestModel('request/C','ui.generation.lastRequestModel')
 for(const locale of ['zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  await requestModel('request/C','ui.generation.lastRequestModel')
  const label=await copy('ui.models.routingCandidate',{model:await copy('ui.components.chat.ModelPicker.601c04f996bd',{p0:'candidate/C',p1:1,p2:1})})
  await page.getByRole('button',{name:label,exact:true}).waitFor()
 }
 await page.evaluate(()=>{window.route('');window.connection('open');window.apply('one',{type:'request/progress',request_id:'one-current',generation:{model:''}})})
 await requestModel(await copy('ui.components.chat.GenerationDock.e403ba5798ba'))
 await page.getByRole('button',{name:await copy('ui.models.routingCandidate',{model:await copy('ui.models.candidateUnknown')}),exact:true}).waitFor()
 await page.evaluate(()=>window.apply('one',{type:'request/end',request_id:'one-current'}))
 await summary.waitFor({state:'detached'})
 assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.method==='PATCH').length),2)
 assert.deepEqual(errors,[]);await page.close()
 console.log('Request model: pending/rejected session and default selections stay distinct from request evidence; full model on touch, routing, retry, old events, conversation switch, disconnect, missing model and locale/theme/size/font matrix passed')
},{entry:'/__request-model-presentation-entry.jsx',vite:{plugins:[{
 name:'request-model-presentation-fixture',resolveId(id){if(id==='/__request-model-presentation-entry.jsx')return id},load(id){if(id==='/__request-model-presentation-entry.jsx')return entry},
 configureServer(server){server.middlewares.use('/__request-model-presentation',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__request-model-presentation','<html><body><div id="root"></div><script type="module" src="/__request-model-presentation-entry.jsx"></script></body></html>'))}catch(error){next(error)}})},
}]}})
