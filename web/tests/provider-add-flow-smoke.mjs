import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry = `
import React from 'react';import {createRoot} from 'react-dom/client';import {ModelConnections} from '/src/components/overlays/ModelConnections.tsx';import {setLocale} from '/src/i18n/index.ts';import '/src/styles.css';setLocale('en');createRoot(document.getElementById('root')).render(React.createElement(ModelConnections));
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage({viewport:{width:390,height:900}});page.setDefaultTimeout(15000)
 const errors=[],writes=[];page.on('pageerror',error=>errors.push(error.message))
 let snapshot={version:2,revision:'empty',providers:[]},authorization
 const catalog=[{id:'custom',name:'Custom API',api_key:true,oauth:false,base_url:'',env:[],models:[]},{id:'anthropic',name:'Native account',api_key:false,oauth:true,base_url:'https://example.invalid',env:[],models:[{id:'remote-native',name:'Native model',api:'anthropic-messages'},{id:'remote-second',name:'Second native model',api:'anthropic-messages'}]}]
 await page.route('**/api/v1/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(path==='/api/v1/connection/providers')return route.fulfill({json:{providers:catalog}})
  if(path==='/api/v1/codex-gateway')return route.fulfill({json:{accounts:[]}})
  if(path==='/api/v1/models')return writes.length===1?route.fulfill({status:503,json:{error:'Fixture catalog unavailable'}}):route.fulfill({json:{models:[],active:''}})
  if(path==='/api/v1/connection/discover')return route.fulfill({status:400,json:{error:{code:'connection/discovery',message:'Fixture discovery unavailable'}}})
  if(path==='/api/v1/connection/authorization'&&request.method()==='POST'){
   authorization=request.postDataJSON();assert.ok(authorization.draft);assert.equal(writes.length,1)
   return route.fulfill({json:{id:'grant',connection_id:authorization.connection_id,status:'pending',events:[],prompt:null,error:''}})
  }
  if(path==='/api/v1/connection/authorization/grant')return route.fulfill({json:{id:'grant',connection_id:authorization.connection_id,status:'completed',events:[],prompt:null,error:''}})
  if(path==='/api/v1/connection'){
   if(request.method()==='GET')return route.fulfill({json:snapshot})
   const body=request.postDataJSON();assert.equal(body.revision,snapshot.revision);writes.push(body)
   snapshot={version:2,revision:'saved-'+writes.length,providers:structuredClone(body.providers)}
   for(const provider of snapshot.providers)for(const connection of provider.connections){connection.auth.configured=connection.auth.type==='oauth';delete connection.auth.authorization_id;for(const key of connection.keys){delete key.value;key.mask='******test';key.available=true}}
   return route.fulfill({json:{routing:snapshot,config:{}}})
  }
  errors.push('Unexpected '+path);return route.fulfill({status:500,json:{error:'Unexpected fixture request'}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__provider-add')
 await page.getByText('No models configured. Choose a provider to get started.',{exact:true}).waitFor()
 await page.getByRole('button',{name:'Choose provider',exact:true}).click();await page.getByRole('textbox',{name:'Search providers',exact:true}).fill('unmatched-search');await page.getByText('No matching providers. Try a different name or choose Custom API.',{exact:true}).waitFor();await page.getByRole('textbox',{name:'Search providers',exact:true}).fill('custom');await page.getByRole('button',{name:'Custom API',exact:true}).click();assert.equal(await page.getByLabel('API key 1',{exact:true}).inputValue(),'');assert.equal(await page.getByRole('textbox',{name:'Enter a model ID',exact:true}).inputValue(),'')
 await page.getByLabel('API endpoint address',{exact:true}).first().fill('invalid-endpoint');await page.getByText('Enter an HTTP or HTTPS base URL without credentials, a query or a fragment.',{exact:true}).waitFor();await page.getByLabel('API endpoint address',{exact:true}).first().fill('https://example.invalid/v1')
 await page.getByLabel('API key 1',{exact:true}).fill('fixture-first-test')
 await page.getByRole('button',{name:'Add API key',exact:true}).click();assert.equal(await page.getByLabel('API key 1',{exact:true}).inputValue(),'******test');await page.getByLabel('API key 2',{exact:true}).fill('fixture-second-test')
 const manual=page.getByRole('textbox',{name:'Enter a model ID',exact:true})
 await manual.fill('https://wrong.example.invalid');assert.equal(await page.getByRole('button',{name:'Add model',exact:true}).isDisabled(),true);await page.getByText('Enter a model ID here. Set the API address in the endpoint field above.',{exact:true}).waitFor();
 const manualEditor=page.getByRole('group',{name:'Add a model manually',exact:true})
 await manual.fill('remote-a');await manual.dispatchEvent('compositionstart');await manual.press('Enter');await manual.dispatchEvent('compositionend');assert.equal(writes.length,0)
 assert.equal(await page.getByRole('group',{name:'0 models selected',exact:true}).locator(':scope > div').count(),0)
 await manualEditor.getByRole('textbox',{name:'Model name',exact:true}).fill('Configured before adding')
 await manualEditor.getByRole('spinbutton',{name:'Context window',exact:true}).fill('0');assert.equal(await page.getByRole('button',{name:'Add model',exact:true}).isDisabled(),true)
 await manualEditor.getByRole('spinbutton',{name:'Context window',exact:true}).fill('128000')
 await manualEditor.getByRole('spinbutton',{name:'Maximum output tokens',exact:true}).fill('8192')
 await manualEditor.getByRole('checkbox',{name:'Image',exact:true}).check()
 await manualEditor.getByRole('combobox',{name:'Reasoning levels',exact:true}).selectOption('custom')
 await manualEditor.getByRole('textbox',{name:'API parameter value for high',exact:true}).fill('large')
 await page.getByRole('button',{name:'Add model',exact:true}).click()
 await page.getByText('Configured before adding',{exact:true}).waitFor();assert.equal(await manual.inputValue(),'');assert.equal(await manual.evaluate(element=>element===document.activeElement),true)
 assert.equal(await manualEditor.getByRole('textbox',{name:'Model name',exact:true}).inputValue(),'')
 assert.equal(await manualEditor.getByRole('spinbutton',{name:'Context window',exact:true}).inputValue(),'')
 assert.equal(await manualEditor.getByRole('checkbox',{name:'Image',exact:true}).isChecked(),false)
 assert.equal(await manualEditor.getByRole('combobox',{name:'Reasoning levels',exact:true}).inputValue(),'inherit')
 await manual.fill('remote-a');assert.equal(await page.getByRole('button',{name:'Add model',exact:true}).isDisabled(),true)
 await manual.fill('remote-b');await manual.press('Enter');await page.getByRole('group',{name:'2 models selected',exact:true}).waitFor()
 await page.getByRole('button',{name:'Fetch available models',exact:true}).click();await page.getByRole('alert').waitFor();await page.getByText('remote-a',{exact:true}).waitFor();await page.getByText('remote-b',{exact:true}).waitFor()
 await page.getByRole('button',{name:'Save',exact:true}).click();await page.getByRole('button',{name:'Edit',exact:true}).waitFor()
 await page.getByText('Configuration saved, but the model menu could not be refreshed: Fixture catalog unavailable',{exact:true}).waitFor();
 assert.equal(writes.length,1);const connection=writes[0].providers[0].connections[0];assert.equal(connection.keys.length,2);assert.equal(connection.models.length,2);assert.equal(connection.models.some(model=>'keys' in model),false)
 const first=connection.models[0],second=connection.models[1]
 assert.equal(first.model,'remote-a');assert.equal(first.name,'Configured before adding');assert.equal(first.context_window,128000);assert.equal(first.max_output_tokens,8192);assert.deepEqual(first.input,['text','image']);assert.equal(first.reasoning_efforts.high,'large')
 assert.equal(second.model,'remote-b');assert.equal(second.name,'remote-b');assert.deepEqual(second.input,['text']);assert.equal(second.context_window,undefined);assert.equal(second.max_output_tokens,undefined);assert.equal(second.reasoning_efforts,undefined)
 await page.getByRole('button',{name:'Edit',exact:true}).click();assert.equal(await page.getByPlaceholder('******test',{exact:true}).count(),2);await page.getByRole('button',{name:'Cancel',exact:true}).click()
 await page.getByRole('button',{name:'Choose provider',exact:true}).click();await page.getByRole('button',{name:'Native account',exact:true}).click();assert.equal(await page.getByLabel('API endpoint address',{exact:true}).inputValue(),'https://example.invalid');await page.getByLabel('API endpoint address',{exact:true}).fill('https://changed.example.invalid/v1');await page.getByRole('button',{name:'Sign-in authorization',exact:true}).click()
 await page.getByText('Authorized. You can sign in again.',{exact:true}).waitFor();assert.equal(writes.length,1)
 const picker=page.getByRole('dialog',{name:/Choose models/})
 await page.getByRole('button',{name:/^Choose models \(/}).click();await picker.waitFor();assert.deepEqual(await picker.getByRole('checkbox').evaluateAll(rows=>rows.map(row=>row.checked)),[false,false]);await picker.getByRole('checkbox').last().check();await picker.getByRole('checkbox').last().uncheck();await picker.getByRole('textbox',{name:'Search models',exact:true}).fill('no-matching-model');await picker.getByText('No matching models. Try another search, or close this page and enter a model ID.',{exact:true}).waitFor();await picker.getByRole('textbox',{name:'Search models',exact:true}).fill('remote');await picker.getByRole('button',{name:'Select all 2',exact:true}).click();await picker.getByRole('button',{name:'Confirm',exact:true}).click();await picker.waitFor({state:'detached'});await page.getByRole('button',{name:'Save',exact:true}).click();await page.getByRole('button',{name:'Edit',exact:true}).nth(1).waitFor()
 assert.equal(writes.length,2);const native=writes[1].providers.find(provider=>provider.type==='anthropic').connections[0];assert.equal(native.base_url,'https://changed.example.invalid/v1');assert.equal(native.auth.authorization_id,'grant');assert.equal(native.models[0].model,'remote-native');assert.equal(native.models.length,2);assert.equal(native.keys.length,0)
 await page.getByRole('button',{name:'Delete Custom API',exact:true}).click();await page.getByRole('button',{name:'Remove provider',exact:true}).waitFor();assert.equal(writes.length,2);await page.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(writes.length,2);
 assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
 await page.close();console.log('Provider addition: empty state, shared keys, manual models, IME, failed probe retention, masked edits and draft OAuth before save passed')
},{entry:'/__provider-add-entry.jsx',vite:{plugins:[{name:'provider-add-fixture',resolveId(id){if(id==='/__provider-add-entry.jsx')return id},load(id){if(id==='/__provider-add-entry.jsx')return entry},configureServer(server){server.middlewares.use('/__provider-add',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__provider-add','<html><body><div id="root"></div><script type="module" src="/__provider-add-entry.jsx"></script></body></html>'))}catch(error){next(error)}})}}]}})
