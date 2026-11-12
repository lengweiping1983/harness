import {openAcademySection} from './academy-sections.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {readFile} from 'node:fs/promises'
import {fixturePython} from './python-fixture.mjs'
import {launchBrowser} from './browser-scenario.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.academy_web_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, page, failure, timer, diagnostics = ''
const releases = []
backend.stderr.on('data', data => {diagnostics += data})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Model focus fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Model focus fixture exited: ' + code + diagnostics)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (Number.isInteger(value.port)) resolve(value)} catch {}})
  })
  clearTimeout(timer)
  const base = 'http://127.0.0.1:' + ready.port
  browser = await launchBrowser()
  page = await browser.newPage({viewport: {width: 1440, height: 1000}})
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(base)
  await page.getByRole('textbox', {name: '输入消息', exact: true}).waitFor()
  await page.keyboard.press('Control+j')
  const helpGroup = page.locator('#inspector-tab-content[data-panel="home"] details').filter({hasText: '学习与帮助'})
  if ((await helpGroup.getAttribute('open')) === null) await helpGroup.locator('summary').click()
  await page.locator('#inspector-tab-content[data-panel="home"]').getByRole('button', {name: '学习中心', exact: true}).click()
  const panel = page.locator('#inspector-tab-content[data-panel="academy"]')
  await panel.locator('[data-course-id="llm-foundations"]').getByRole('button').click()
  await panel.locator('[data-academy-study-action]').waitFor()
  const get = async path => (await page.request.get(base + '/api/v1/academy' + path)).json()
  const project = (await get('/projects')).projects[0]
  const lesson = (await get('/courses/llm-foundations')).course.lessons.find(item => item.mode === 'model')
  await openAcademySection(panel, 'manage')
  await panel.getByRole('combobox', {name: '课程目录', exact: true}).selectOption(lesson.id)
  await panel.getByRole('button', {name: '保存当前版本并打开本课', exact: true}).click()
  let prompt = panel.getByRole('textbox', {name: '实验提示词', exact: true})
  let copy=JSON.parse(await readFile(new URL('../src/i18n/locales/zh-CN.json',import.meta.url),'utf-8'))
  let content='Keyboard model experiment / 模型输入\n'+Array.from({length:70},(_,index)=>'Line '+index+' / 保留输入与选区\n').join('')
  await prompt.fill(content)
  const endpoint='**/api/v1/academy/projects/'+project.id+'/model-runs'
  let writes=0
  page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/model-runs'))writes++})
  const waitReady=async()=>{
    await page.waitForFunction(()=>sessionStorage.getItem('harness.academy.pending.v1')===null&&document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled===false)
  }
  const select=async()=>{
    await waitReady()
    // 点击经过真实控件的稳定性与可用性检查，避免在响应式布局切换中途提交。
    await prompt.click()
    await prompt.evaluate(node=>{node.setSelectionRange(4,12,'backward');node.scrollTop=35})
    return prompt.evaluate(node=>({start:node.selectionStart,end:node.selectionEnd,direction:node.selectionDirection,top:node.scrollTop,left:node.scrollLeft}))
  }
  const restore=async(expected)=>{
    await page.waitForFunction(label=>{const node=[...document.querySelectorAll('textarea')].find(node=>node.getAttribute('aria-label')===label);return node&&!node.disabled&&document.activeElement===node&&!sessionStorage.getItem('harness.academy.pending.v1')},copy['ui.academy.modelPrompt'])
    assert.deepEqual(await prompt.evaluate(node=>({start:node.selectionStart,end:node.selectionEnd,direction:node.selectionDirection,top:node.scrollTop,left:node.scrollLeft})),expected)
    assert.equal(await prompt.inputValue(),content)
    await waitReady()
  }
  let releaseRead,readEntered
  const heldRead=new Promise(resolve=>{releaseRead=resolve;releases.push(resolve)})
  const enteredRead=new Promise(resolve=>{readEntered=resolve})
  await page.route(endpoint,async route=>{
    if(route.request().method()==='GET'&&writes>0){readEntered();await heldRead}
    await route.continue()
  })
  const expected=await select()
  const accepted=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/model-runs'))
  await prompt.press('Control+Enter')
  await accepted
  await enteredRead
  assert.equal(await prompt.evaluate(node=>node===document.activeElement),false,'Receipt alone cannot restore focus before an actual run read')
  releaseRead()
  await restore(expected)
  await page.unroute(endpoint)
  for(const language of ['en','zh-CN','en']){
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language=>document.documentElement.lang===language&&!document.getElementById('language-preference').disabled,language)
    await page.keyboard.press('Control+,')
    copy=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url),'utf-8'))
    prompt=panel.getByRole('textbox',{name:copy['ui.academy.modelPrompt'],exact:true})
    content+='Own input / '+language+'\n'
    await prompt.fill(content)
    for(const theme of ['light','dark']){
      await page.setViewportSize({width:theme==='light'?390:1440,height:1000})
      await page.evaluate(theme=>document.documentElement.className=theme,theme)
      await page.locator('.app-shell[data-mode="'+(theme==='light'?'overlay':'columns')+'"]').waitFor()
      const expected=await select()
      await prompt.press(theme==='light'?'Control+Enter':'Meta+Enter')
      await restore(expected)
    }
  }
  let releaseWrite,writeEntered
  const heldWrite=new Promise(resolve=>{releaseWrite=resolve;releases.push(resolve)})
  const enteredWrite=new Promise(resolve=>{writeEntered=resolve})
  await page.route(endpoint,async route=>{
    if(route.request().method()!=='POST')return route.continue()
    const response=await route.fetch();writeEntered();await heldWrite;await route.fulfill({response})
  })
  await select();await prompt.press('Control+Enter');await enteredWrite
  const guide=panel.locator('details[tabindex="-1"] > summary')
  await guide.click()
  releaseWrite();await waitReady()
  assert.equal(await prompt.evaluate(node=>node===document.activeElement),false,'A later click must win over focus restoration')
  await page.unroute(endpoint)
  const focusBeforeButton=await prompt.evaluate(node=>node===document.activeElement)
  assert.equal(focusBeforeButton,false)
  await panel.getByRole('button',{name:copy['ui.academy.modelRun'],exact:true}).click()
  await waitReady()
  assert.equal(await prompt.evaluate(node=>node===document.activeElement),false,'Button submission must not move edit focus')
  await page.route(endpoint,async route=>{
    if(route.request().method()!=='POST')return route.continue()
    await route.fetch();await route.abort('failed')
  })
  await select()
  const lost=page.waitForEvent('requestfailed',request=>request.method()==='POST'&&request.url().endsWith('/model-runs'))
  await prompt.press('Control+Enter');await lost
  const beforeRecovery=writes
  await page.unroute(endpoint)
  await panel.getByRole('button',{name:copy['ui.academy.recheckReceipt'],exact:true}).click()
  await waitReady()
  assert.equal(await prompt.evaluate(node=>node===document.activeElement),false)
  assert.equal(writes,beforeRecovery,'Manual recovery cannot replay the model request')
  await page.route(endpoint,async route=>{
    if(route.request().method()!=='POST')return route.continue()
    const response=await route.fetch(),receipt=await response.json()
    receipt.result.model_run.prompt='WRONG_RECEIPT_INPUT'
    await route.fulfill({response,json:receipt})
  })
  await select();await prompt.press('Control+Enter')
  await panel.getByRole('alert').waitFor()
  const wrongReceiptWrites=writes
  await page.unroute(endpoint)
  await panel.getByRole('button',{name:copy['ui.academy.recheckReceipt'],exact:true}).click()
  await waitReady()
  assert.equal(await prompt.evaluate(node=>node===document.activeElement),false,'A corrected receipt cannot revive a rejected focus intent')
  assert.equal(writes,wrongReceiptWrites)
  let releaseHidden,hiddenEntered
  const heldHidden=new Promise(resolve=>{releaseHidden=resolve;releases.push(resolve)})
  const enteredHidden=new Promise(resolve=>{hiddenEntered=resolve})
  await page.route(endpoint,async route=>{
    if(route.request().method()!=='POST')return route.continue()
    const response=await route.fetch();hiddenEntered();await heldHidden;await route.fulfill({response})
  })
  const hiddenResponse=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/model-runs'))
  await select();await prompt.press('Control+Enter');await enteredHidden
  await page.keyboard.press('Control+j')
  await page.waitForFunction(()=>document.querySelector('#task-inspector')?.getAttribute('aria-hidden')==='true')
  releaseHidden()
  await hiddenResponse
  await page.unroute(endpoint)
  await page.keyboard.press('Control+j')
  await panel.getByRole('button',{name:copy['ui.academy.recheckReceipt'],exact:true}).click()
  await waitReady()
  assert.equal(await prompt.evaluate(node=>node===document.activeElement),false,'A hidden page cannot regain a stale edit intent')
  assert.equal(await prompt.inputValue(),content)
  assert.deepEqual((await get('/projects/'+project.id+'/runs')).runs,[])
  assert.deepEqual(errors,[])
  console.log('Academy model keyboard focus: confirmed record read, selection/scroll, Ctrl/Meta shortcuts, en/zh/en responsive themes, pointer/button/hidden isolation and lost/mismatched receipts without replay verified')
} catch(error){failure=error}
finally{
  clearTimeout(timer)
  await cleanupFixture({error:failure,browser,lines,backend,releases})
}
