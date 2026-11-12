import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entryPath='/__connector-search-entry.js'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ConnectorsTab} from '/src/components/overlays/ConnectorsTab.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.language=setLocale;window.copy=t;window.writes=[]
const connectors=[
 {name:'OriginalRemote',metadata:{display_name:'Original 名称',description:'Original calendar description'},transport:'http',url:'https://example.test/mcp',enabled:false,bound:false,connected:false,tool_count:0},
 {name:'OriginalLocal',metadata:{description:'Original file description'},transport:'stdio',command:'local-command',args:[],enabled:false,bound:false,connected:false,tool_count:0}
]
const root=createRoot(document.getElementById('root'))
window.populate=(empty=false)=>root.render(React.createElement(ConnectorsTab,{overview:{skills:[],agents:[],connectors:empty?[]:connectors,enabled:{connectors:true}},envMcp:[],busy:'',perform:async(key)=>{window.writes.push(key);return false}}))
await setLocale('en');window.populate()
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{
  requests.push({method:route.request().method(),url:route.request().url()})
  return route.fulfill({json:{presets:[{name:'OriginalPreset',description:'Original recommendation',transport:'http',installed:false}]}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__connector-search')
 const copy=(key,args)=>page.evaluate(({key,args})=>window.copy(key,args),{key,args})
 const search=page.getByRole('searchbox')
 await search.waitFor()
 await page.waitForFunction(()=>document.querySelectorAll('[role="switch"]').length===2)
 for(const width of [390,1440])for(const dark of [false,true])for(const language of ['en','zh-CN','en']){
  await page.setViewportSize({width,height:900})
  await page.evaluate(async({language,dark})=>{await window.language(language);document.documentElement.classList.toggle('dark',dark)},{language,dark})
  assert.equal(await search.getAttribute('aria-label'),await copy('ui.extensions.searchConnectors'))
  const recommendations=language==='en'?'Recommended connectors':'推荐连接器'
  await page.locator('summary').filter({hasText:recommendations}).waitFor()
  const add=page.getByRole('button',{name:language==='en'?'Add a connector':'添加连接器',exact:true})
  await add.click()
  await page.getByRole('textbox',{name:language==='en'?'Connector name':'连接器名称',exact:true}).waitFor()
  await add.click()
  for(const query of ['OriginalRemote','Original 名称','calendar','example.test','local-command','  ORIGINAL   file  ']){
   await search.fill(query)
   assert.equal(await page.getByRole('switch').count(),1)
   assert.equal(await page.getByRole('status').innerText(),await copy('ui.esc.search.count',{count:1,total:2}))
  }
  await search.fill('missing connector')
  await page.getByText(await copy('ui.connectors.noMatches'),{exact:true}).waitFor()
  await page.getByText(await copy('ui.connectors.noMatchesHint'),{exact:true}).waitFor()
  assert.equal(await page.getByText(await copy('ui.components.overlays.EscPanel.c0b1a386e295'),{exact:true}).count(),0)
  assert.equal(await page.getByRole('status').innerText(),await copy('ui.esc.search.count',{count:0,total:2}))
  if(language==='zh-CN'&&!dark&&width===1440&&process.env.HARNESS_TEST_CONNECTOR_SEARCH_SCREENSHOT)await page.screenshot({path:process.env.HARNESS_TEST_CONNECTOR_SEARCH_SCREENSHOT})
  await page.getByRole('button',{name:await copy('ui.esc.search.clear'),exact:true}).click()
  assert.equal(await search.inputValue(),'')
  assert.equal(await page.getByRole('switch').count(),2)
  await page.evaluate(()=>window.populate(true))
  await page.getByText(await copy('ui.components.overlays.EscPanel.c0b1a386e295'),{exact:true}).waitFor()
  await page.getByText(await copy('ui.components.overlays.EscPanel.b322bdce8ba9'),{exact:true}).waitFor()
  assert.equal(await page.getByText(await copy('ui.connectors.noMatches'),{exact:true}).count(),0)
  assert.equal(await page.getByRole('status').count(),0)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
  await page.evaluate(()=>window.populate())
  await page.waitForFunction(()=>document.querySelectorAll('[role="switch"]').length===2)
 }
 assert.deepEqual(await page.evaluate(()=>window.writes),[])
 assert.equal(requests.length,1,'Search, locale and empty-state changes do not reread the catalog')
 assert.equal(requests[0].method,'GET')
 assert.deepEqual(errors,[])
 console.log('Connector search: original names, descriptions, commands and URLs; distinct empty states, counts and reset; bilingual themes and widths without writes or rereads')
},{entry:entryPath,vite:{plugins:[{name:'connector-search-fixture',resolveId(id){if(id===entryPath)return id},load(id){if(id===entryPath)return entry},configureServer(server){server.middlewares.use('/__connector-search',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__connector-search','<html><body><div id="root"></div><script type="module" src="'+entryPath+'"></script></body></html>'))}catch(error){next(error)}})}}]}})
