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
    timer = setTimeout(() => reject(Error('Quiz help fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Quiz help fixture exited: ' + code + diagnostics)))
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
  const question = panel.getByRole('textbox', {name:'向带练助手提问',exact:true})
  const draft = 'Keep my own question / 保留问题草稿'
  await question.fill(draft)
  const first = (await get('/courses/llm-foundations')).course.lessons[0]
  const check = first.knowledge_checks[0]
  const wrong = check.options.find(option => option.id !== check.answer_id)
  const quiz = panel.locator('[data-academy-check-id="' + check.id + '"]')
  await quiz.getByRole('radio', {name:wrong.label,exact:true}).check()
  await quiz.getByRole('button', {name:'核对答案',exact:true}).click()
  let copy
  for(const language of ['en','zh-CN','en']){
    await page.keyboard.press('Control+,')
    await page.locator('#language-preference').selectOption(language)
    await page.waitForFunction(language => document.documentElement.lang === language && !document.getElementById('language-preference').disabled,language)
    await page.keyboard.press('Control+,')
    copy=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url),'utf-8'))
    await quiz.getByRole('button',{name:copy['ui.academy.tutor.quizHelp'],exact:true}).waitFor()
    assert.equal(await quiz.locator('input:checked').inputValue(),wrong.id)
    assert.equal(await panel.getByRole('textbox',{name:copy['ui.academy.tutor.question'],exact:true}).inputValue(),draft)
  }
  const currentCheck=(await get('/courses/llm-foundations')).course.lessons[0].knowledge_checks[0]
  let asks=0, otherWrites=0
  page.on('request',request=>{
    if(request.method()!=='POST')return
    if(request.url().endsWith('/tutor-turns'))asks++
    else if(request.url().includes('/academy/'))otherWrites++
  })
  const help=quiz.getByRole('button',{name:copy['ui.academy.tutor.quizHelp'],exact:true})
  await help.dispatchEvent('compositionstart')
  await help.click()
  assert.equal(asks,0)
  await help.dispatchEvent('compositionend')
  const endpoint='**/api/v1/academy/projects/'+project.id+'/tutor-turns'
  await page.route(endpoint,async route=>{
    if(route.request().method()!=='POST')return route.continue()
    const body=route.request().postDataJSON()
    assert.deepEqual(body.knowledge_check,{check_id:check.id,option_id:wrong.id})
    assert.equal(body.allow_execution,false)
    assert.equal(body.source,undefined)
    await route.fetch();await route.abort('failed')
  })
  const lost=page.waitForEvent('requestfailed',request=>request.method()==='POST'&&request.url().endsWith('/tutor-turns'))
  await help.evaluate(button=>{button.click();button.click()})
  await lost
  assert.equal(asks,1)
  const pending=JSON.parse(await page.evaluate(()=>sessionStorage.getItem('harness.academy.pending.v1')))
  assert.deepEqual(pending.tutorInput.knowledge_check,{check_id:check.id,option_id:wrong.id})
  await page.unroute(endpoint)
  const receiptEndpoint='**/api/v1/academy/receipts/'+pending.requestId
  await page.route(receiptEndpoint,async route=>{
    const response=await route.fetch(),value=await response.json()
    value.result.tutor_turn.knowledge_check.option_id='different-option'
    await route.fulfill({response,json:value})
  })
  await panel.getByRole('button',{name:copy['ui.academy.recheckReceipt'],exact:true}).click()
  await panel.getByRole('alert').waitFor()
  assert.ok(await page.evaluate(()=>sessionStorage.getItem('harness.academy.pending.v1')))
  await page.unroute(receiptEndpoint)
  await page.reload()
  await page.locator('.app-shell').waitFor()
  if(await page.locator('#task-inspector').getAttribute('aria-hidden')==='true')await page.keyboard.press('Control+j')
  await panel.getByRole('button',{name:copy['ui.academy.recheckReceipt'],exact:true}).click()
  await page.waitForFunction(()=>sessionStorage.getItem('harness.academy.pending.v1')===null)
  await panel.getByLabel(copy['ui.academy.tutor.answer'],{exact:true}).waitFor()
  const turn=(await get('/projects/'+project.id+'/tutor-turns')).tutor_turns[0]
  assert.equal(turn.status,'completed')
  assert.deepEqual(turn.knowledge_check,{check_id:check.id,option_id:wrong.id})
  assert.ok(turn.response.includes(currentCheck.prompt))
  assert.ok(turn.response.includes(currentCheck.options.find(option=>option.id===wrong.id).label))
  assert.deepEqual(turn.actions,[])
  assert.equal(await panel.getByRole('textbox',{name:copy['ui.academy.tutor.question'],exact:true}).inputValue(),draft)
  assert.deepEqual((await get('/projects/'+project.id+'/runs')).runs,[])
  assert.equal((await get('/projects/'+project.id)).project.progress[0].state,'in_progress')
  assert.equal(asks,1)
  assert.equal(otherWrites,0)
  assert.equal(await quiz.getByRole('radio',{checked:true}).count(),0)
  await panel.getByRole('textbox',{name:copy['ui.academy.tutor.question'],exact:true}).fill('Why? / 为什么？')
  const followupResponse=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/tutor-turns'))
  await panel.getByRole('button',{name:copy['ui.academy.tutor.send'],exact:true}).click()
  const followupReceipt=await (await followupResponse).json()
  const followupId=followupReceipt.result.tutor_turn.id
  await panel.locator('[data-tutor-turn-id="'+followupId+'"]').getByLabel(copy['ui.academy.tutor.answer'],{exact:true}).waitFor()
  const followup=(await get('/projects/'+project.id+'/tutor-turns')).tutor_turns[0]
  assert.equal(followup.knowledge_check,undefined,'A follow-up does not add a new explicit selection or permission')
  assert.ok(followup.response.includes(currentCheck.prompt))
  assert.ok(followup.response.includes(currentCheck.options.find(option=>option.id===wrong.id).label))
  assert.deepEqual(followup.actions,[])
  assert.equal(asks,2)
  assert.equal(otherWrites,0)
  await openAcademySection(panel,'manage')
  await panel.locator('[data-course-id="harness-foundations"]').getByRole('button').click()
  const sourceEditor=panel.locator('textarea[spellcheck="false"]')
  await sourceEditor.waitFor()
  const originalSource=await sourceEditor.inputValue()
  const codeProject=(await get('/projects')).projects.find(item=>item.course_id==='harness-foundations')
  const codeLesson=(await get('/courses/harness-foundations')).course.lessons[0]
  const codeCheck=codeLesson.knowledge_checks[0]
  await openAcademySection(panel,'quiz')
  const codeQuiz=panel.locator('[data-academy-check-id="'+codeCheck.id+'"]')
  const codeWrong=codeCheck.options.find(option=>option.id!==codeCheck.answer_id)
  await codeQuiz.getByRole('radio',{name:codeWrong.label,exact:true}).check()
  await codeQuiz.getByRole('button',{name:copy['ui.academy.checkUnderstanding'],exact:true}).click()
  const unsaved=originalSource+'\n# Unsaved while discussing a quiz / 尚未保存\n'
  await sourceEditor.fill(unsaved)
  asks=0;otherWrites=0
  await codeQuiz.getByRole('button',{name:copy['ui.academy.tutor.quizHelp'],exact:true}).click()
  await panel.getByLabel(copy['ui.academy.tutor.answer'],{exact:true}).waitFor()
  const codeTurn=(await get('/projects/'+codeProject.id+'/tutor-turns')).tutor_turns[0]
  assert.deepEqual(codeTurn.knowledge_check,{check_id:codeCheck.id,option_id:codeWrong.id})
  assert.equal(await sourceEditor.inputValue(),unsaved)
  assert.equal((await get('/projects/'+codeProject.id+'/source?path=main.py')).content,originalSource)
  assert.equal(asks,1)
  assert.equal(otherWrites,0,'Quiz help cannot save or execute the dirty source')
  assert.deepEqual(codeTurn.actions,[])
  assert.deepEqual(errors,[])
  console.log('Academy quiz help: frozen selected question/choice, one native readonly turn, IME/double-click guards, preserved question, wrong-scope receipt recovery after reload, no grading/execution and en/zh/en verified')
} catch(error){failure=error}
finally{
  clearTimeout(timer)
  await cleanupFixture({error:failure,browser,lines,backend,releases})
}
