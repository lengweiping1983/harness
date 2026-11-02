import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const catalog=Array.from({length:396},(_,index)=>({id:`group-${index<90?'a':'b'}-${String(index).padStart(4,'0')}`,name:`Model ${String(index).padStart(4,'0')}`,contextWindow:128000,maxTokens:8192}))
const initial=catalog.slice(0,243).map((row,index)=>({id:`stable-${index}`,model:row.id,name:index===0?'Custom model name':row.name,enabled:index!==0,input:['text'],context_window:index===0?12345:128000}))
const connection={id:'endpoint',name:'Large catalog',base_url:'https://example.invalid/v1',protocol:'openai-completions',enabled:true,auth:{type:'api_key',env:{}},keys:[{id:'key',enabled:true,available:true,mask:'******test'}],model_catalog:catalog,models:initial}
const entry=`import React from 'react';import {createRoot} from 'react-dom/client';import {ModelConnections} from '/src/components/overlays/ModelConnections.tsx';import {setLocale,t} from '/src/i18n/index.ts';import '/src/styles.css';window.locale=setLocale;window.copy=t;await setLocale('en');createRoot(document.getElementById('root')).render(React.createElement(ModelConnections));`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(15000)
 const errors=[],writes=[];page.on('pageerror',error=>errors.push(error.message))
 let snapshot={version:2,revision:'initial',providers:[{id:'provider',type:'custom',name:'Stored provider',connections:[connection]}]}
 await page.route('**/api/v1/**',route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(path==='/api/v1/connection/providers')return route.fulfill({json:{providers:[{id:'custom',name:'Large catalog API',base_url:connection.base_url,api_key:true,oauth:false,env:[],models:catalog}]}})
  if(path==='/api/v1/codex-gateway')return route.fulfill({json:{accounts:[]}})
  if(path==='/api/v1/models')return route.fulfill({json:{models:[],active:''}})
  if(path==='/api/v1/connection'){
   if(request.method()==='GET')return route.fulfill({json:snapshot})
   const body=request.postDataJSON();assert.equal(body.revision,snapshot.revision);writes.push(body)
   snapshot={version:2,revision:`saved-${writes.length}`,providers:structuredClone(body.providers)}
   for(const provider of snapshot.providers)for(const endpoint of provider.connections)for(const key of endpoint.keys){delete key.value;key.mask='******test';key.available=true}
   return route.fulfill({json:{routing:snapshot,config:{}}})
  }
  errors.push(path);return route.fulfill({status:500,json:{error:'Unexpected fixture request'}})
 })
 const copy=key=>page.evaluate(key=>window.copy('ui.providers.'+key),key)
 const label=(key,values)=>page.evaluate(({key,values})=>window.copy('ui.providers.'+key,values),{key,values})
 const button=async key=>page.getByRole('button',{name:await copy(key),exact:true})
 const counts=async count=>page.getByText(await page.evaluate(count=>window.copy('ui.providers.modelCounts',{total:396,selected:count}),count),{exact:true}).last().waitFor()
 const selected=()=>page.getByRole('group',{name:/models selected|已选择.*个模型/})
 const picker=()=>page.getByRole('dialog',{name:/Choose models|选择多个模型/})
 const openPicker=async()=>{await page.getByRole('button',{name:/^(Choose models|选择模型)/}).click();await picker().waitFor()}
 const closePicker=async confirm=>{await picker().getByRole('button',{name:await copy(confirm?'confirm':'cancel'),exact:true}).click();await picker().waitFor({state:'detached'})}
 const pickerSearch=async value=>{const row=picker().getByRole('textbox',{name:await copy('searchModels'),exact:true});await row.fill(value);return row}
 const pickerNavigation=()=>picker().getByRole('navigation',{name:/Choose models|选择多个模型/})
 await page.goto(server.resolvedUrls.local[0]+'__provider-large')
 // 选择供应商在独立弹层内完成，选中后关闭并展开该供应商的编辑表单
 await (await button('add')).click()
 await page.getByRole('dialog',{name:await copy('chooseProviderTitle'),exact:true}).waitFor()
 await page.getByRole('button',{name:'Large catalog API',exact:true}).click();await picker().waitFor({state:'detached'})
 await counts(0)
 await openPicker()
 assert.equal(await picker().getByRole('button',{name:await label('clearCount',{count:396}),exact:true}).isDisabled(),true)
 await closePicker(false)
 await (await button('cancel')).click();assert.equal(writes.length,0)
 await (await button('edit')).click();await counts(243)
 assert.equal(await selected().locator(':scope > div').count(),10)
 const selectedNavigation=page.getByRole('navigation',{name:/models selected|已选择.*个模型/})
 await selectedNavigation.getByRole('button',{name:'Next page',exact:true}).click()
 await selectedNavigation.getByText('Showing 11–20 of 243',{exact:true}).waitFor()
 const selectedSearch=page.getByRole('textbox',{name:'Search selected models',exact:true})
 await selectedSearch.fill('group-a-0089');assert.equal(await selected().locator(':scope > div').count(),1)
 await selected().getByText('Model options',{exact:true}).click()
 await selected().getByRole('textbox',{name:'Model name',exact:true}).fill('Edited page model')
 await selected().getByRole('button',{name:'Move up',exact:true}).click()
 await selectedSearch.fill('');await selectedNavigation.getByText('Showing 1–10 of 243',{exact:true}).waitFor()
 // 模型列表弹层：每页 50 条，批量操作按当前筛选结果执行且按钮带数量
 await openPicker()
 assert.equal(await picker().getByRole('checkbox').count(),50)
 await pickerNavigation().getByRole('button',{name:'Next page',exact:true}).click();await pickerNavigation().getByText('Showing 51–100 of 396',{exact:true}).waitFor()
 await pickerSearch('group-b');await pickerNavigation().getByText('Showing 1–50 of 306',{exact:true}).waitFor()
 await picker().getByRole('button',{name:await label('clearCount',{count:306}),exact:true}).click();await closePicker(true);await counts(90)
 await openPicker();await pickerSearch('group-b')
 await picker().getByText(await label('matchingModels',{count:306}),{exact:true}).waitFor()
 assert.equal(await picker().getByRole('checkbox').count(),50)
 await picker().getByRole('button',{name:await label('selectAllCount',{count:306}),exact:true}).click();await closePicker(true);await counts(396)
 assert.equal(await selected().locator(':scope > div').count(),10)
 await openPicker()
 assert.equal(await picker().getByRole('button',{name:await label('selectAllCount',{count:396}),exact:true}).isDisabled(),true)
 await closePicker(false)
 // 手工填写模型 ID 在接口卡上直接可见
 await page.getByRole('textbox',{name:'Enter a model ID',exact:true}).fill('manual-outside-catalog');await (await button('addModel')).click();await counts(397)
 await openPicker();await pickerSearch('group-b')
 await picker().getByRole('button',{name:await label('clearCount',{count:306}),exact:true}).click();await closePicker(true);await counts(91)
 await openPicker();await pickerSearch('group-b')
 await picker().getByRole('button',{name:await label('selectAllCount',{count:306}),exact:true}).click();await closePicker(true);await counts(397)
 await selectedSearch.fill('group-a-0089')
 await selected().getByText('Edited page model',{exact:true}).waitFor()
 await selectedSearch.fill('no-such-selected-model')
 await page.getByText(await copy('noSelectedModels'),{exact:true}).waitFor()
 await selectedSearch.fill('group-a-0089')
 await (await button('save')).click();await (await button('edit')).waitFor();assert.equal(writes.length,1)
 const saved=writes[0].providers[0].connections[0].models
 assert.equal(saved.length,397);assert.deepEqual(saved[0],initial[0])
 assert.equal(saved[88].id,'stable-89');assert.equal(saved[89].id,'stable-88');assert.equal(saved[88].name,'Edited page model')
 assert.equal(saved.find(row=>row.model==='manual-outside-catalog').name,'manual-outside-catalog')
 // 切换语言保留弹层内的搜索、已选搜索与分页
 await (await button('edit')).click();await counts(397)
 await openPicker();await pickerSearch('group-b')
 await selectedSearch.fill('manual-outside-catalog')
 await mkdir('../output/provider-large',{recursive:true})
 for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:1000});await page.evaluate(async({language,theme})=>{await window.locale(language);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{language,theme})
  await counts(397)
  assert.equal(await picker().getByRole('textbox',{name:await copy('searchModels'),exact:true}).inputValue(),'group-b')
  assert.equal(await page.getByRole('textbox',{name:await copy('searchSelectedModels'),exact:true}).inputValue(),'manual-outside-catalog')
  await selected().getByText('manual-outside-catalog',{exact:true}).waitFor()
  assert.equal(await picker().getByRole('checkbox').count(),50)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
  assert.equal(writes.length,1)
  if(language==='zh-CN')await page.screenshot({path:`../output/provider-large/models-${theme}-${width}.png`,fullPage:true})
 }
 // 全不选只取消当前筛选结果里已选的目录模型，手工填写的模型保留，需单独删除
 await closePicker(false)
 await openPicker();await pickerSearch('')
 await picker().getByRole('button',{name:await label('clearCount',{count:396}),exact:true}).click();await closePicker(true)
 await counts(1)
 await selected().getByText('manual-outside-catalog',{exact:true}).waitFor()
 await selected().getByRole('button',{name:'Delete model',exact:true}).click();await counts(0)
 await openPicker()
 assert.equal(await picker().getByRole('button',{name:await label('clearCount',{count:396}),exact:true}).isDisabled(),true)
 await closePicker(false)
 await (await button('cancel')).click();assert.equal(writes.length,1)
 await (await button('edit')).click();await counts(397)
 await openPicker()
 await picker().getByRole('button',{name:await label('clearCount',{count:396}),exact:true}).click();await closePicker(true)
 await selected().getByText('manual-outside-catalog',{exact:true}).waitFor()
 await selected().getByRole('button',{name:'Delete model',exact:true}).click()
 await (await button('save')).click();await (await button('edit')).waitFor();assert.equal(writes.length,2)
 assert.deepEqual(writes[1].providers[0].connections[0].models,[])
 await (await button('edit')).click();await counts(0)
 await openPicker();await pickerSearch('not-in-the-catalog')
 assert.equal(await picker().getByRole('checkbox').count(),0)
 assert.equal(await picker().getByRole('button',{name:await label('selectAllCount',{count:0}),exact:true}).isDisabled(),true)
 assert.equal(await picker().getByRole('button',{name:await label('clearCount',{count:0}),exact:true}).isDisabled(),true)
 await picker().getByText(await copy('noModels'),{exact:true}).waitFor()
 assert.deepEqual(errors,[]);await page.close()
 console.log('Large model catalog: 396 candidates, 243 saved selections, provider picker, empty defaults, bounded pages, filtered bulk actions with counts, manual models preserved by deselect, search, global ordering, cancel/save/readback and language/theme/width matrix passed')
},{entry:'/__provider-large-entry.jsx',vite:{plugins:[{name:'provider-large-fixture',resolveId(id){if(id==='/__provider-large-entry.jsx')return id},load(id){if(id==='/__provider-large-entry.jsx')return entry},configureServer(server){server.middlewares.use('/__provider-large',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__provider-large','<html><body><div id="root"></div><script type="module" src="/__provider-large-entry.jsx"></script></body></html>'))}catch(error){next(error)}})}}]}})
