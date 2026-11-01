import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile} from 'node:fs/promises'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'
import {cleanupFixture,withDeadline} from './fixture-cleanup.mjs'
const copy=JSON.parse(await readFile(new URL('../src/i18n/locales/zh-CN.json',import.meta.url),'utf8'))
const backend=spawn(fixturePython,['-m','tests.web_fixture'],{cwd:fileURLToPath(new URL('../../',import.meta.url)),stdio:['ignore','pipe','pipe']})
const lines=createInterface({input:backend.stdout});let browser,timer,failure,diagnostics=''
backend.stderr.on('data',value=>{diagnostics+=value})
try{
 const ready=await new Promise((resolve,reject)=>{
  timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+diagnostics)),20000)
  backend.once('error',reject);backend.once('exit',code=>reject(Error('Fixture exited: '+code)))
  lines.on('line',line=>{try{const data=JSON.parse(line);if(data.port)resolve(data)}catch{}})
 });clearTimeout(timer)
 browser=await launchBrowser();const url='http://127.0.0.1:'+ready.port
 const cold=await browser.newPage();await cold.clock.install();let initial,initialEntered
 const initialRequested=new Promise(resolve=>{initialEntered=resolve})
 await cold.route('**/assets/zh-CN-*.js',route=>{initial=route;initialEntered()})
 await cold.goto(url,{waitUntil:'domcontentloaded'})
 await cold.getByRole('status').filter({hasText:copy['ui.App.f76a8d866552']}).waitFor()
 await withDeadline(()=>initialRequested,30000,'Initial locale request')
 assert.ok(initial,'the current language chunk must be requested')
 await cold.clock.runFor(10001)
 await cold.getByRole('alert').filter({hasText:copy['ui.startup.failed']}).waitFor()
 await initial.continue()
 await cold.getByRole('textbox',{name:'输入消息',exact:true}).waitFor()
 await cold.close()
 const page=await browser.newPage(),requests=[],errors=[];let english,englishEntered
 const englishRequested=new Promise(resolve=>{englishEntered=resolve})
 page.on('pageerror',error=>errors.push(error.message))
 page.on('request',request=>{if(/\/assets\/(en|zh-CN)-[^/]+\.js/.test(request.url()))requests.push(request.url())})
 await page.route('**/assets/en-*.js',route=>{english=route;englishEntered()})
 await page.clock.install();await page.goto(url)
 await page.getByRole('textbox',{name:'输入消息',exact:true}).waitFor()
 assert.equal(requests.filter(value=>/\/en-/.test(value)).length,0)
 await page.keyboard.press('Control+,');const preference=page.locator('#language-preference')
 await preference.selectOption('en')
 await withDeadline(()=>englishRequested,30000,'English locale request')
 assert.ok(english)
 assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN')
 assert.equal(await preference.isDisabled(),true)
 await page.clock.runFor(10001)
 await page.getByRole('alert').filter({hasText:copy['settings.language.loadFailed']}).waitFor()
 assert.equal(await preference.isDisabled(),false)
 const response=page.waitForResponse('**/assets/en-*.js');await english.continue();await (await response).finished()
 assert.equal(await page.locator('html').getAttribute('lang'),'zh-CN','late download must not silently apply a failed operation')
 await page.getByRole('button',{name:copy['settings.language.reload'],exact:true}).click()
 await page.waitForFunction(()=>document.documentElement.lang==='en')
 await preference.selectOption('zh-CN');await page.waitForFunction(()=>document.documentElement.lang==='zh-CN')
 await preference.selectOption('en');await page.waitForFunction(()=>document.documentElement.lang==='en')
 assert.equal(requests.filter(value=>/\/en-/.test(value)).length,1)
 assert.deepEqual(errors,[]);await page.close()
 const en=await browser.newPage(),coldRequests=[]
 await en.addInitScript(()=>localStorage.setItem('harness.language.effective.v1','en'))
 en.on('request',request=>{if(/\/assets\/(en|zh-CN)-[^/]+\.js/.test(request.url()))coldRequests.push(request.url())})
 await en.goto(url);await en.getByRole('textbox',{name:'Input message',exact:true}).waitFor()
 assert.equal(await en.locator('html').getAttribute('lang'),'en')
 assert.equal(coldRequests.filter(value=>/\/zh-CN-/.test(value)).length,0)
 assert.equal(coldRequests.filter(value=>/\/en-/.test(value)).length,1)
 await en.close()
 console.log('Production cold starts load one locale; startup timeout recovers; switch timeout preserves the visible language and explicit readback applies the late resource without repeating the write')
}catch(error){failure=error}finally{clearTimeout(timer);await cleanupFixture({error:failure,browser,lines,backend})}
