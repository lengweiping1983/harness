import assert from 'node:assert/strict'
import {spawn,execFileSync} from 'node:child_process'
import {mkdtemp, mkdir, rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {createInterface} from 'node:readline'
import {chromium} from 'playwright-core'
import {resolveBrowserExecutable} from './browser-executable.mjs'
import {fixturePython} from './python-fixture.mjs'
import {assertCompanionLayout} from './chatgpt-companion-layout.mjs'

const root=fileURLToPath(new URL('../../',import.meta.url))
const temporary=await mkdtemp(path.join(tmpdir(),'harness-chat-extension-'))
const extension=path.join(temporary,'extension')
let context,child
const messages=[],waiters=[]
let failure
try{
 const archive=path.join(temporary,'companion.zip')
 execFileSync(fixturePython,[path.join(root,'scripts/package_chat_companion.py'),'--output',archive],{cwd:root})
 execFileSync(fixturePython,['-c','import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])',archive,extension],{cwd:root})
 child=spawn(fixturePython,['-m','tests.chat_companion_fixture',temporary],{cwd:root,env:process.env,stdio:['pipe','pipe','pipe']})
 let diagnostics='';child.stderr.on('data',data=>diagnostics+=data.toString())
 const lines=createInterface({input:child.stdout})
 lines.on('line',line=>{try{const item=JSON.parse(line);messages.push(item);for(const notify of waiters.splice(0))notify()}catch{}})
 const next=async predicate=>{
  const deadline=Date.now()+15000
  while(Date.now()<deadline){
   const found=messages.find(predicate);if(found)return found
   if(child.exitCode!==null)throw Error('Fixture exited: '+diagnostics)
   await Promise.race([new Promise(resolve=>waiters.push(resolve)),new Promise(resolve=>setTimeout(resolve,100))])
  }
  throw Error('Fixture readiness timeout: '+diagnostics)
 }
 const fixture=await next(value=>value.port)
 context=await chromium.launchPersistentContext(path.join(temporary,'browser'),{headless:true,
  executablePath:resolveBrowserExecutable(),args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]})
 const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker')
 const id=new URL(worker.url()).host
 await worker.evaluate(()=>{
  globalThis.__observedMessages=[];
  chrome.runtime.onMessage.addListener((message,sender)=>{globalThis.__observedMessages.push({type:message.type,url:sender.url,id:sender.id})})
 })
 const url='https://chatgpt.com/c/00000000-0000-0000-0000-000000000001'
 await context.route('https://chatgpt.com/**',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html><body><main>
 <div data-turn-key="assistant-1"><div data-talvt-turn-state="complete"><div><h4 data-conversation-role="assistant">ChatGPT said</h4><p>Ready to continue.</p></div></div></div>
 <form data-chatgpt-composer hidden><div contenteditable="true" role="textbox">Hidden draft</div></form>
 <form data-chatgpt-composer onsubmit="return false"><div contenteditable="true" role="textbox" data-composer-markdown></div><input type="file" multiple><button type="button" aria-label="开始语音">Voice</button></form>
 </main><script>
 const editor=document.querySelector('form:not([hidden]) [role="textbox"]');let sequence=0;
 editor.addEventListener('input',()=>{
  if(!editor.textContent.trim()||editor.parentElement.querySelector('[type="submit"]'))return;
  const button=document.createElement('button');button.type='submit';button.ariaLabel='发送';
  button.onclick=event=>{event.preventDefault();const turn=document.createElement('div');turn.dataset.turnKey='sent-'+(++sequence);
   const body=document.createElement('div'),heading=document.createElement('h4'),text=document.createElement('p');
   heading.dataset.conversationRole='user';heading.textContent='You said';text.textContent=editor.textContent;
   body.append(heading,text);turn.append(body);document.querySelector('main').append(turn);editor.textContent='';button.remove();};
  editor.parentElement.append(button);
 });</script></body></html>`}))
 const chat=await context.newPage();await chat.goto(url)
 const popup=await context.newPage()
 popup.on('pageerror',error=>console.error('Extension popup:',error.message))
 popup.on('console',message=>{if(message.type()==='error')console.error('Extension console:',message.text())})
 await popup.goto(`chrome-extension://${id}/popup.html`)
 await popup.waitForFunction(()=>!document.querySelector('#connect').disabled)
 const chatId=await popup.evaluate(async url=>(await chrome.tabs.query({})).find(tab=>tab.url===url).id,url)
 for(const language of ['en','zh_CN','en']){
  await popup.locator('#language').selectOption(language)
  await popup.locator('#connect').waitFor()
  await popup.waitForFunction(language=>document.querySelector('#connect').textContent===(language==='en'?'Connect and load projects':'连接并读取项目'),language)
  for(const colorScheme of ['light','dark']){
   await popup.emulateMedia({colorScheme})
   await popup.setViewportSize({width:760,height:600})
   await assertCompanionLayout(popup,'popup')
   assert.equal(await popup.locator('label[for="token"]').textContent(),language==='en'?'Pairing token':'配对码')
   assert.equal(await popup.locator('#language').getAttribute('aria-label'),language==='en'?'Language':'语言')
   assert.equal(await popup.locator('#version').textContent(),'1.0.0')
   assert.match(await popup.locator('[data-key="timing"]').textContent(),/120[–～]180/)
   assert.equal(await popup.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
   assert.equal(await popup.evaluate(()=>document.documentElement.scrollHeight<=innerHeight),true)
   assert.equal(await popup.evaluate(()=>document.querySelector('.panels').getBoundingClientRect().height<330),true)
  }
 }
 // 旧语言读取迟到不能覆盖一次英文→中文→英文切换后的当前界面。
 await popup.evaluate(()=>{
  const original=window.fetch.bind(window)
  window.__originalLocaleFetch=original
  window.__localeHeld=false;window.__localeReleased=false
  window.fetch=async(...args)=>{
   const response=await original(...args)
   if(String(args[0]).includes('/zh_CN/')){
    return {json:async()=>{
     const value=await response.json()
     window.__localeHeld=true
     await new Promise(resolve=>{window.__releaseLocale=resolve})
     window.__localeReleased=true
     return value
    }}
   }
   return response
  }
 })
 await popup.locator('#language').selectOption('zh_CN')
 await popup.waitForFunction(()=>window.__localeHeld)
 await popup.locator('#connect').evaluate(node=>{node.textContent='Current language pending'})
 await popup.locator('#language').selectOption('en')
 await popup.waitForFunction(()=>document.querySelector('#connect').textContent==='Connect and load projects')
 await popup.evaluate(()=>window.__releaseLocale())
 await popup.waitForFunction(()=>window.__localeReleased)
 assert.equal(await popup.locator('#connect').textContent(),'Connect and load projects')
 await popup.evaluate(()=>{window.fetch=window.__originalLocaleFetch})
 // 设置读取迟到同样不能逆转已选择的语言，也不能丢失其他连接设置。
 await popup.evaluate(()=>{
  const get=chrome.storage.local.get.bind(chrome.storage.local),set=chrome.storage.local.set.bind(chrome.storage.local)
  window.__languageHeld=false;window.__languageSaves=0
  window.__restoreLanguageStorage=()=>{chrome.storage.local.get=get;chrome.storage.local.set=set}
  chrome.storage.local.get=async keys=>{
   if(keys==='settings'&&!window.__languageHeld){window.__languageHeld=true;await new Promise(resolve=>{window.__releaseLanguageStorage=resolve})}
   return get(keys)
  }
  chrome.storage.local.set=async value=>{await set(value);if(value.settings)window.__languageSaves++}
 })
 await popup.locator('#language').selectOption('zh_CN')
 await popup.waitForFunction(()=>window.__languageHeld)
 await popup.locator('#language').selectOption('en')
 await popup.waitForFunction(()=>document.querySelector('#connect').textContent==='Connect and load projects')
 await popup.evaluate(()=>window.__releaseLanguageStorage())
 await popup.waitForFunction(()=>window.__languageSaves===2)
 assert.equal(await popup.evaluate(async()=>(await chrome.storage.local.get('settings')).settings.language),'en')
 assert.equal(await popup.locator('#language').inputValue(),'en')
 assert.equal(await popup.locator('#connect').textContent(),'Connect and load projects')
 await popup.evaluate(()=>window.__restoreLanguageStorage())
 await popup.locator('#endpoint').fill(`http://127.0.0.1:${fixture.port}/`)
 await popup.locator('#token').fill('wrong-token')
 await popup.locator('#connect').click()
 await popup.waitForFunction(()=>document.querySelector('#connection-status').textContent.includes('rejected this pairing token'))
 assert.equal(await popup.locator('#start').isDisabled(),true)
 for(const language of ['zh_CN','en']){
  await popup.locator('#language').selectOption(language)
  await popup.waitForFunction(language=>document.querySelector('#connection-status').textContent.includes(language==='en'?'rejected this pairing token':'接续桥拒绝了此配对码'),language)
  assert.equal(await popup.locator('#start').isDisabled(),true)
 }
 await popup.locator('#token').fill(fixture.token)
 let releaseHealth,notifyHealth
 const healthHeld=new Promise(resolve=>{notifyHealth=resolve})
 const healthRelease=new Promise(resolve=>{releaseHealth=resolve})
 const healthRoute=async route=>{const response=await route.fetch();notifyHealth();await healthRelease;await route.fulfill({response})}
 await context.route(`http://127.0.0.1:${fixture.port}/health`,healthRoute)
 try{
  await popup.locator('#connect').click()
  await Promise.race([healthHeld,new Promise((_,reject)=>setTimeout(()=>reject(Error('Health check was not accepted')),5000))])
  await popup.locator('#stop').click()
  await popup.waitForFunction(()=>document.querySelector('#connection-status').textContent==='Enter the bridge address and pairing token to check the connection.')
 }finally{releaseHealth()}
 await popup.waitForFunction(()=>!document.querySelector('#connect').disabled)
 await context.unroute(`http://127.0.0.1:${fixture.port}/health`,healthRoute)
 assert.equal(await popup.locator('#session option').count(),0)
 assert.equal(await popup.locator('#start').isDisabled(),true)
 await popup.locator('#connect').click()
 try{await popup.waitForFunction(()=>document.querySelector('#session').options.length===1
  || document.querySelector('#connection-status').textContent.includes('failed'))}
 catch(error){throw new Error('Extension connect: '+await popup.locator('#connection-status').textContent()
  +' messages='+JSON.stringify(await worker.evaluate(()=>globalThis.__observedMessages)),{cause:error})}
 assert.equal(await popup.locator('#session option').count(),1,await popup.locator('#status').textContent())
 assert.equal(await popup.locator('#session').inputValue(),'fixture')
 assert.equal(await popup.locator('.status-card').getAttribute('data-state'),'connected')
 for(const language of ['en','zh_CN','en']){
  await popup.locator('#language').selectOption(language)
  await popup.waitForFunction(language=>document.documentElement.lang===(language==='en'?'en':'zh-CN'),language)
  assert.equal(await popup.evaluate(()=>document.documentElement.scrollHeight<=innerHeight),true)
 }
 await popup.setViewportSize({width:760,height:320})
 assert.equal(await popup.evaluate(()=>document.documentElement.scrollHeight>innerHeight),true)
 await popup.locator('#start').scrollIntoViewIfNeeded()
 await popup.goto(`chrome-extension://${id}/popup.html?view=tab&sourceTab=${chatId}&sourceChat=${encodeURIComponent(url)}`)
 await popup.waitForFunction(()=>!document.querySelector('#connect').disabled)
 await popup.setViewportSize({width:390,height:900})
 await assertCompanionLayout(popup,'tab')
 await popup.locator('#help summary').click()
 assert.equal(await popup.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 assert.equal(await popup.evaluate(()=>document.documentElement.scrollHeight>innerHeight),true)
 await popup.locator('#help p').last().scrollIntoViewIfNeeded()
 assert.equal(await popup.locator('#help p').last().evaluate(node=>node.getBoundingClientRect().bottom<=innerHeight),true)
 await popup.locator('#start').scrollIntoViewIfNeeded()
 assert.equal(await popup.locator('#start').evaluate(node=>node.getBoundingClientRect().top>=0&&node.getBoundingClientRect().bottom<=innerHeight),true)
 await popup.locator('#help summary').click()
 await popup.goto(`chrome-extension://${id}/popup.html`)
 await popup.waitForFunction(()=>!document.querySelector('#connect').disabled)
 await popup.setViewportSize({width:760,height:600})
 await popup.evaluate(async id=>chrome.tabs.update(id,{active:true}),chatId)
 // 绑定已受理但响应尚未返回时，停止仍须阻止迟到结果开启接续。
 let releaseBind,notifyBind
 const bindHeld=new Promise(resolve=>{notifyBind=resolve})
 const bindRelease=new Promise(resolve=>{releaseBind=resolve})
 const bindRoute=async route=>{const response=await route.fetch();notifyBind();await bindRelease;await route.fulfill({response})}
 await context.route(`http://127.0.0.1:${fixture.port}/bind`,bindRoute)
 try{
  await popup.locator('#start').click()
  await Promise.race([bindHeld,new Promise((_,reject)=>setTimeout(()=>reject(Error('Bind was not accepted')),5000))])
  await popup.locator('#stop').click()
  await popup.waitForFunction(()=>document.querySelector('#status').textContent==='Continuation paused.')
 }finally{releaseBind()}
 await popup.waitForFunction(()=>!document.querySelector('#start').disabled)
 await context.unroute(`http://127.0.0.1:${fixture.port}/bind`,bindRoute)
 assert.notEqual(await popup.evaluate(async()=>(await chrome.storage.local.get('binding')).binding?.enabled),true)
 assert.equal(await chat.locator('h4[data-conversation-role="user"]').count(),0)
 await popup.locator('#start').click()
 // 等待的是内容脚本的空闲稳定窗口，不是猜测业务完成。
 await new Promise(resolve=>setTimeout(resolve,3200))
 await popup.evaluate(()=>chrome.runtime.sendMessage({type:'tick'}))
 await chat.locator('h4[data-conversation-role="user"]').waitFor()
 assert.equal(await chat.locator('h4[data-conversation-role="user"]').count(),1)
 assert.equal(await chat.locator('[data-message-author-role="assistant"]').count(),0)
 assert.match(await popup.locator('#checked-at').textContent(),/Last check:/)
 await popup.evaluate(()=>chrome.runtime.sendMessage({type:'tick'}))
 assert.equal(await chat.locator('h4[data-conversation-role="user"]').count(),1)
 await chat.locator('form:not([hidden]) [role="textbox"]').fill('User draft must stay')
 child.stdin.write('progress\n');await next(value=>value.progress)
 await popup.evaluate(()=>chrome.runtime.sendMessage({type:'tick'}))
 assert.equal(await chat.locator('form:not([hidden]) [role="textbox"]').textContent(),'User draft must stay')
 assert.equal(await chat.locator('h4[data-conversation-role="user"]').count(),1)
 await popup.locator('#stop').click()
 assert.equal(await popup.evaluate(async()=>(await chrome.storage.local.get('binding')).binding.enabled),false)
 await popup.evaluate(async()=>{
  const {binding}=await chrome.storage.local.get('binding');await chrome.storage.local.set({binding:{...binding,reason:'reply_unrecognized'}})
 })
 for(const language of ['en','zh_CN','en']){
  await popup.locator('#language').selectOption(language)
  await popup.waitForFunction(language=>document.querySelector('#status').textContent.includes(language==='en'?'No assistant reply was recognized':'尚未识别到助手回复'),language)
  assert.match(await popup.locator('#checked-at').textContent(),language==='en'?/Last check:/ : /上次检查：/)
 }
 await popup.reload()
 await popup.waitForFunction(()=>document.querySelector('#session').options.length===1&&!document.querySelector('#connect').disabled)
 assert.equal(await popup.locator('#session').inputValue(),'fixture')
 assert.equal(await popup.evaluate(async()=>(await chrome.storage.local.get('binding')).binding.enabled),false)
 await mkdir(path.join(root,'output'),{recursive:true})
 await popup.locator('#language').selectOption('zh_CN')
 await popup.waitForFunction(()=>document.documentElement.lang==='zh-CN')
 await popup.emulateMedia({colorScheme:'light'})
 await popup.locator('#stop').click()
 assert.equal(await popup.evaluate(()=>document.documentElement.scrollHeight<=innerHeight),true)
 await popup.screenshot({path:path.join(root,'output/chat-companion-panel.png'),fullPage:true})
 console.log('Packaged extension: root manifest, real MV3 worker, pairing diagnostics, late binding stopped, restored connection, send receipt, no replay, bilingual themes and draft protection passed')
}catch(error){failure=error}
finally{
 const errors=[]
 if(context)try{await context.close()}catch(error){errors.push(error)}
 if(child&&child.exitCode===null){
  child.stdin.write('stop\n')
  try{await Promise.race([new Promise((resolve,reject)=>child.once('exit',code=>code===0?resolve():reject(Error('Fixture exit '+code)))),new Promise((_,reject)=>setTimeout(()=>reject(Error('Fixture cleanup timeout')),10000))])}
  catch(error){errors.push(error);child.kill()}
 }
 try{await rm(temporary,{recursive:true,force:true})}catch(error){errors.push(error)}
 if(failure)errors.unshift(failure)
 if(errors.length===1)throw errors[0]
 if(errors.length>1)throw new AggregateError(errors,'Chat extension scenario and cleanup failed')
}
