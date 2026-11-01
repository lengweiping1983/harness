import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {readFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'
const root=fileURLToPath(new URL('../../',import.meta.url))
const backend=spawn(fixturePython,['-m','tests.web_fixture'],{cwd:root,stdio:['ignore','pipe','pipe']})
const lines=createInterface({input:backend.stdout});let browser,timer,diagnostics=''
backend.stderr.on('data',value=>{diagnostics+=value})
try{
 const ready=await new Promise((resolve,reject)=>{
  timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+diagnostics)),20000)
  backend.once('error',reject);backend.once('exit',code=>reject(Error('Fixture exited: '+code)))
  lines.on('line',line=>{try{const value=JSON.parse(line);if(value.port)resolve(value)}catch{}})
 });clearTimeout(timer)
 browser=await launchBrowser();const base='http://127.0.0.1:'+ready.port
 const strings=Object.fromEntries(await Promise.all(['en','zh-CN'].map(async language=>[language,JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))])))
 for(const kind of ['extension','empty','base64']){
  const page=await browser.newPage();page.setDefaultTimeout(15000)
  const errors=[];let uploads=0,wire;page.on('pageerror',error=>errors.push(error.message))
  assert.ok((await page.request.patch(base+'/api/v1/ui-settings',{data:{language:'en'}})).ok())
  await page.route('**/api/v1/attachments',async route=>{
   uploads++
   const file={name:kind==='extension'?'Original 原文.exe':'User 原文.txt',mediaType:'text/plain',data:kind==='empty'?'':kind==='base64'?'!!!!':'YQ=='}
   const response=await route.fetch({postData:JSON.stringify({files:[file]})});wire=await response.json()
   assert.equal(response.status(),400);assert.ok(wire.details.copy.key.startsWith('ui.attachments.server.'))
   await route.fulfill({response})
  })
  await page.goto(base)
  await page.locator('textarea[aria-autocomplete=list]').waitFor()
  const rejected=page.waitForResponse(response=>response.url().endsWith('/api/v1/attachments')&&response.status()===400)
  await page.evaluate(()=>{const transfer=new DataTransfer();transfer.items.add(new File(['User content'],'User 原文.txt'));window.dispatchEvent(new DragEvent('drop',{dataTransfer:transfer,bubbles:true,cancelable:true}))})
  await rejected
  const item=page.getByRole('listitem').filter({hasText:'User 原文.txt'})
  await item.waitFor()
  for(const language of ['en','zh-CN','en']){
   await page.keyboard.press('Control+,');await page.locator('#language-preference').selectOption(language)
   await page.waitForFunction(language=>document.documentElement.lang===language,language)
   await page.keyboard.press('Control+,')
   const copy=wire.details.copy
   const expected=strings[language][copy.key].replace(/\{([^}]+)\}/g,(token,key)=>Object.hasOwn(copy.params,key)?String(copy.params[key]):token)
   for(const theme of ['light','dark'])for(const width of [390,1440]){
    await page.setViewportSize({width,height:900});await page.evaluate(theme=>{document.documentElement.className=theme;document.documentElement.dataset.theme=theme},theme)
    await page.waitForFunction(expected=>Array.from(document.querySelectorAll('[role=listitem]')).some(element=>element.getAttribute('title')===expected),expected)
    assert.equal(uploads,1);assert.ok((await item.innerText()).includes('User 原文.txt'))
   }
  }
  assert.deepEqual(errors,[]);await page.close();console.log(kind+': real backend resource rejection survives actual language settings changes without replay')
 }
}finally{
 clearTimeout(timer);if(browser)await browser.close();lines.close()
 if(backend.exitCode===null&&backend.signalCode===null){const exited=new Promise(resolve=>backend.once('exit',resolve));backend.kill('SIGTERM');await exited}
}
