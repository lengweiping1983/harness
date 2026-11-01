import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const entry=`import React from 'react';import {createRoot} from 'react-dom/client';import {ModelConnections} from '/src/components/overlays/ModelConnections.tsx';import {setLocale,t} from '/src/i18n/index.ts';import '/src/styles.css';setLocale('en');window.locale=setLocale;window.copy=t;createRoot(document.getElementById('root')).render(React.createElement(ModelConnections));`
const templates=[['opencode-zen','OpenCode Zen'],['openrouter','OpenRouter'],['opencode-local','OpenCode Local'],['deepseek','DeepSeek'],['openai','OpenAI'],['opencode-go','OpenCode Go']].map(([id,name])=>({id,name,base_url:'https://example.invalid/v1',api_key:true,oauth:false,env:[],models:[{id:'remote',name:'Remote'}]}))
const provider={id:'supplier',type:'deepseek',name:'DeepSeek',connections:[{id:'endpoint',name:'DeepSeek',base_url:'https://example.invalid/v1',protocol:'',enabled:true,auth:{type:'api_key',env:{}},keys:[{id:'key',mask:'******last',enabled:true,available:true}],models:[{id:'model',model:'remote',name:'Remote',enabled:true,input:['text']}]}]}
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage();page.setDefaultTimeout(15000);const errors=[],writes=[];page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/**',route=>{
  const path=new URL(route.request().url()).pathname
  if(route.request().method()!=='GET'){writes.push(path);return route.fulfill({status:500,json:{error:'No writes expected'}})}
  if(path==='/api/v1/connection')return route.fulfill({json:{version:2,revision:'fixture',providers:[provider],models:[],current:null,cycle:1}})
  if(path==='/api/v1/connection/providers')return route.fulfill({json:{providers:templates}})
  if(path==='/api/v1/codex-gateway')return route.fulfill({json:{accounts:[],enabled:false,auto_rotate:true,current_account:'',threshold:95,waiting:0}})
  if(path==='/api/v1/codex-gateway/desktop')return route.fulfill({json:{enabled:false,config_path:'/fixture'}})
  if(path==='/api/v1/models')return route.fulfill({json:{models:[],active:'',providers:[]}})
  errors.push(path);return route.fulfill({status:500,json:{error:'Unexpected fixture request'}})
 })
 await page.goto(server.resolvedUrls.local[0]+'__provider-order')
 await page.getByRole('button',{name:'Edit',exact:true}).waitFor()
 assert.equal((await page.locator('body').innerText()).includes('connection not tested'),false)
 await page.getByRole('button',{name:'Edit',exact:true}).click();assert.equal(await page.getByRole('button',{name:'Remove connection',exact:true}).count(),0);assert.equal(await page.getByText('Unsaved',{exact:true}).count(),0)
 assert.equal(await page.getByRole('button',{name:'Add another endpoint',exact:true}).isVisible(),false);await page.getByText('Additional endpoint settings',{exact:true}).click();await page.getByRole('button',{name:'Add another endpoint',exact:true}).click();await page.getByRole('button',{name:'Remove connection',exact:true}).first().waitFor();assert.equal(await page.getByRole('button',{name:'Remove connection',exact:true}).count(),2)
 const header=await page.getByRole('button',{name:'Remove connection',exact:true}).last().evaluate(button=>{const summary=button.closest('details').querySelector('summary');return Math.abs(button.getBoundingClientRect().top-summary.getBoundingClientRect().top)})
 assert.ok(header<=4,'Remove action belongs in the connection header')
 await page.getByRole('button',{name:'Remove connection',exact:true}).last().click();assert.equal(await page.getByRole('button',{name:'Remove connection',exact:true}).count(),0)
 await page.getByRole('button',{name:'Cancel',exact:true}).click();await page.getByRole('button',{name:'Choose provider',exact:true}).click()
 const providerDialog=()=>page.getByRole('dialog',{name:/Choose provider|选择提供商/})
 const options=()=>providerDialog().locator('div.grid').getByRole('button')
 await providerDialog().waitFor()
 await mkdir('../output/provider-order',{recursive:true})
 for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900});await page.evaluate(({language,theme})=>{window.locale(language);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{language,theme})
  const pool=await page.evaluate(()=>window.copy('ui.codexGateway.accountsTitle'))
  assert.deepEqual(await options().allTextContents(),['DeepSeek','OpenAI',pool,'OpenCode Go','OpenCode Local','OpenCode Zen','OpenRouter'])
  assert.equal(await page.getByRole('button',{name:'ChatGPT',exact:true}).count(),0)
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)
  if(language==='zh-CN'&&width===1440)await page.screenshot({path:`../output/provider-order/choices-${theme}.png`,fullPage:true})
 }
 const search=page.getByRole('textbox',{name:'Search providers',exact:true})
 await search.fill('opencode');assert.deepEqual(await options().allTextContents(),['OpenCode Go','OpenCode Local','OpenCode Zen'])
 for(const query of ['openai','chatgpt','codex']){await search.fill(query);await page.getByRole('button',{name:'Codex account pool',exact:true}).waitFor()}
 await search.fill('')
 await page.getByRole('button',{name:'Codex account pool',exact:true}).click();await page.getByRole('heading',{name:'Codex account pool',exact:true}).waitFor()
 await page.evaluate(()=>window.locale('zh-CN'));await page.getByRole('heading',{name:'Codex 帐号池',exact:true}).waitFor()
 assert.equal((await page.locator('body').innerText()).includes('尚未测试连接'),false)
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);await page.close();console.log('Provider choices: ordered families, grouped OpenCode, OpenAI Codex account pool, no placeholder statuses and functional compact multi-connection deletion passed')
},{entry:'/__provider-order-entry.jsx',vite:{plugins:[{name:'provider-order-fixture',resolveId(id){if(id==='/__provider-order-entry.jsx')return id},load(id){if(id==='/__provider-order-entry.jsx')return entry},configureServer(server){server.middlewares.use('/__provider-order',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__provider-order','<html><body><div id="root"></div><script type="module" src="/__provider-order-entry.jsx"></script></body></html>'))}catch(error){next(error)}})}}]}})
