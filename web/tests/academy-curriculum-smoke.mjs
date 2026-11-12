import {requireAcademySandbox} from './academy-environment.mjs'
import {openAcademySection} from './academy-sections.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {mkdir,readFile} from 'node:fs/promises'
import {fixturePython} from './python-fixture.mjs'
import {launchBrowser} from './browser-scenario.mjs'
import {cleanupFixture} from './fixture-cleanup.mjs'

const root=fileURLToPath(new URL('../../',import.meta.url))
const chineseResources=JSON.parse(await readFile(new URL('../src/i18n/locales/zh-CN.json',import.meta.url),'utf-8'))
const invalidResponse=chineseResources['ui.http.invalidResponse']
const backend=spawn(fixturePython,['-m','tests.academy_web_fixture'],{cwd:root,stdio:['ignore','pipe','pipe']})
const lines=createInterface({input:backend.stdout})
let diagnostics='',browser,failure,timer
const releases=[]
backend.stderr.on('data',data=>{diagnostics+=data})
try{
  const ready=await new Promise((resolve,reject)=>{
    timer=setTimeout(()=>reject(Error('Curriculum fixture startup timed out: '+diagnostics)),20000)
    backend.once('error',reject)
    backend.once('exit',code=>reject(Error('Curriculum fixture exited: '+code+diagnostics)))
    lines.on('line',line=>{try{const value=JSON.parse(line);if(Number.isInteger(value.port))resolve(value)}catch{}})
  })
  clearTimeout(timer)
  const base='http://127.0.0.1:'+ready.port
  browser=await launchBrowser()
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  const get=async path=>(await page.request.get(base+'/api/v1/academy'+path)).json()
  await page.goto(base)
  await page.getByRole('textbox',{name:'输入消息',exact:true}).waitFor()
  await page.keyboard.press('Control+j')
  const helpGroup=page.locator('#inspector-tab-content[data-panel="home"] details').filter({hasText:'学习与帮助'})
  if ((await helpGroup.getAttribute('open')) === null) await helpGroup.locator('summary').click()
  await page.locator('#inspector-tab-content[data-panel="home"]').getByRole('button',{name:'学习中心',exact:true}).click()
  const panel=page.locator('#inspector-tab-content[data-panel="academy"]')
  const disclose=async title=>{
    const summary=panel.locator('summary').filter({hasText:title}).first()
    if(await summary.locator('..').getAttribute('open')===null)await summary.click()
  }
  const python=panel.locator('[data-course-id="python-foundations"]')
  await python.getByRole('heading',{name:'Python：从第一行输出到独立小功能',exact:true}).waitFor()
  const startPython=python.getByRole('button',{name:'开始课程：Python：从第一行输出到独立小功能',exact:true})
  await startPython.click({trial:true})
  await startPython.dispatchEvent('compositionstart')
  await startPython.click()
  assert.equal((await get('/projects')).projects.length,0,'Composition cannot create a learning project')
  await startPython.dispatchEvent('compositionend')
  for(const keyCode of [13,229]){
    const prevented=await startPython.evaluate((button,keyCode)=>!button.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode,isComposing:keyCode===13,bubbles:true,cancelable:true})),keyCode)
    assert.equal(prevented,true)
  }
  await startPython.evaluate(button=>{button.click();button.click()})
  const editor=panel.getByRole('textbox',{name:'练习代码',exact:true})
  await editor.waitFor()
  assert.ok((await editor.inputValue()).includes('learner ='))
  const firstProject=(await get('/projects')).projects[0]
  await requireAcademySandbox(firstProject)
  const check=panel.getByRole('button',{name:'检查练习',exact:true})
  await check.click()
  await panel.getByText('本课检查通过',{exact:true}).waitFor()
  // 完成后只需一次显式继续操作；回执丢失和刷新不能重复切课。
  const pythonCourse=(await get('/courses/python-foundations')).course
  assert.equal(await panel.locator('[data-academy-task]').textContent(),pythonCourse.lessons[0].exercise)
  const nextLesson=pythonCourse.lessons[1]
  const continueName=chineseResources['ui.academy.continueAction'].replace('{order}',nextLesson.order).replace('{title}',nextLesson.title)
  const continueButton=panel.getByRole('button',{name:continueName,exact:true})
  await continueButton.waitFor()
  assert.equal((await get('/projects/'+firstProject.id)).project.active_lesson_id,pythonCourse.lessons[0].id,'A passed check cannot automatically switch lessons')
  await editor.fill((await editor.inputValue())+'\n# 未保存的学生草稿\n')
  await continueButton.waitFor({state:'detached'})
  assert.equal(await continueButton.count(),0,'An unsaved draft cannot expose the completed-input continuation')
  await panel.getByRole('button',{name:'放弃未保存修改',exact:true}).click()
  await continueButton.waitFor()
  let continuePosts=0
  const continuePath='**/api/v1/academy/projects/'+firstProject.id+'/lessons/'+nextLesson.id+'/start'
  await page.route(continuePath,async route=>{
    continuePosts++
    await route.fetch()
    await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({code:'fixture/lost-receipt',error:'The lesson was opened but the response was lost.'})})
  })
  await continueButton.click({trial:true})
  await continueButton.dispatchEvent('compositionstart');await continueButton.click()
  assert.equal(continuePosts,0)
  await continueButton.dispatchEvent('compositionend')
  for(const keyCode of [13,229]){
    assert.equal(await continueButton.evaluate((button,keyCode)=>!button.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode,isComposing:keyCode===13,bubbles:true,cancelable:true})),keyCode),true)
  }
  await continueButton.evaluate(button=>{button.click();button.click()})
  await panel.getByRole('button',{name:'回查操作回执',exact:true}).waitFor()
  const pending=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('harness.academy.pending.v1')))
  assert.equal(pending.targetLessonId,nextLesson.id)
  await page.reload()
  await page.getByRole('textbox',{name:'输入消息',exact:true}).waitFor()
  if(await page.locator('#task-inspector').getAttribute('aria-hidden')==='true')await page.keyboard.press('Control+j')
  await panel.getByRole('button',{name:'回查操作回执',exact:true}).waitFor()
  await page.route('**/api/v1/academy/receipts/'+pending.requestId,async route=>{
    const response=await route.fetch(),body=await response.json()
    body.result.project.active_lesson_id=pythonCourse.lessons[0].id
    await route.fulfill({response,json:body})
  })
  await panel.getByRole('button',{name:'回查操作回执',exact:true}).click()
  await panel.getByRole('alert').filter({hasText:invalidResponse}).waitFor()
  assert.equal(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('harness.academy.pending.v1')).targetLessonId),nextLesson.id,'A different active lesson cannot confirm the requested transition')
  await page.unroute('**/api/v1/academy/receipts/'+pending.requestId)
  await panel.getByRole('button',{name:'回查操作回执',exact:true}).click()
  await page.waitForFunction(()=>sessionStorage.getItem('harness.academy.pending.v1')===null)
  await page.unroute(continuePath)
  await editor.waitFor()
  const continued=(await get('/projects/'+firstProject.id)).project
  assert.equal(continued.active_lesson_id,nextLesson.id)
  const savedVersions=(await get('/projects/'+firstProject.id+'/checkpoints')).checkpoints
  assert.ok(savedVersions.some(row=>row.kind==='before-switch'&&row.lesson_id===pythonCourse.lessons[0].id&&row.fingerprint===firstProject.fingerprint))
  assert.equal(continuePosts,1,'Duplicate clicks, refresh and readback cannot replay the accepted lesson transition')
  const lessons=panel.getByRole('combobox',{name:'课程目录',exact:true})
  await page.waitForFunction(()=>document.querySelector('button[aria-keyshortcuts="Control+Enter Meta+Enter"]')?.disabled===false)
  let releaseProject,projectEntered,projectHandled,heldProjectRequest,projectReads=0
  const projectGate=new Promise(resolve=>{releaseProject=resolve})
  const projectObserved=new Promise(resolve=>{projectEntered=resolve})
  const projectDone=new Promise(resolve=>{projectHandled=resolve})
  releases.push(releaseProject)
  const projectPath='**/api/v1/academy/projects/'+firstProject.id
  await page.route(projectPath,async route=>{
    if(++projectReads!==1){await route.continue();return}
    heldProjectRequest=route.request()
    const response=await route.fetch()
    projectEntered()
    await projectGate
    try{await route.fulfill({response})}catch(error){if(!route.request().failure())throw error}finally{projectHandled()}
  })
  await panel.getByRole('button',{name:'刷新学习状态',exact:true}).click()
  await projectObserved
  assert.equal(heldProjectRequest.failure(),null,'The held read must still be active before navigating')
  const cancelledProject=page.waitForEvent('requestfailed',request=>request===heldProjectRequest)
  await openAcademySection(panel, 'manage')
  await lessons.selectOption('clean-text')
  await cancelledProject
  releaseProject()
  await projectDone
  await page.unroute(projectPath)
  await panel.getByRole('button',{name:'保存当前版本并打开本课',exact:true}).click()
  await editor.waitFor()
  await check.click()
  await panel.getByText('本课检查未通过',{exact:true}).waitFor()
  const starter=await editor.inputValue()
  assert.ok(starter.includes('clean_name = name\n'))
  assert.equal(await panel.locator('[data-academy-task]').textContent(),pythonCourse.lessons.find(lesson=>lesson.id==='clean-text').exercise,'The task must follow the lesson actually opened')
  await editor.fill(starter.replace('clean_name = name\n','clean_name = name.strip()\n'))
  await openAcademySection(panel, 'code-options')
  await panel.getByRole('button',{name:'保存修改',exact:true}).click()
  await check.click()
  await panel.getByText('本课检查通过',{exact:true}).waitFor()
  let projects=(await get('/projects')).projects
  assert.equal(projects.length,1,'One course click creates one project without a name form')
  const pythonProject=projects[0]
  assert.equal(pythonProject.course_id,'python-foundations')
  assert.equal(pythonProject.progress.find(row=>row.lesson_id==='clean-text').state,'passed')
  await disclose('课程与项目')
  const llm=panel.locator('[data-course-id="llm-foundations"]')
  await llm.getByRole('button',{name:'开始课程：大模型：从提示到可核验回答',exact:true}).click()
  const complete=panel.locator('[data-academy-study-action]')
  await complete.waitFor()
  assert.equal(await panel.locator('[data-academy-task]').count(),0,'Reading activities keep their task with the visible guide')
  assert.equal(await panel.getByRole('heading',{name:'动手试一试',exact:true}).isVisible(),true)
  assert.equal(await editor.count(),0)
  assert.equal(await check.count(),0)
  assert.equal(await complete.getAttribute('data-study-step'),'quiz')
  await panel.getByRole('radio',{name:'能。缺信息总是表示关闭。',exact:true}).check()
  await panel.getByRole('button',{name:'核对答案',exact:true}).click()
  await panel.getByText('换个角度，再试一次。',{exact:true}).waitFor()
  await panel.getByRole('radio',{name:'不能。依据这段材料，周日时间仍未知。',exact:true}).check()
  await panel.getByRole('button',{name:'核对答案',exact:true}).click()
  await panel.getByRole('textbox',{name:'学习说明',exact:true}).fill('周一 09:00 有材料支持，周日开放时间仍未知，应查询图书馆官方时间表。')
  await complete.click()
  await panel.getByText('本课学习活动已完成',{exact:true}).waitFor()
  // 模型课使用真实实验接口，原始输入与回答成为独立学习证据。
  const llmCourse=(await get('/courses/llm-foundations')).course
  const llmNext=llmCourse.lessons[1]
  const llmContinueName=chineseResources['ui.academy.continueAction'].replace('{order}',llmNext.order).replace('{title}',llmNext.title)
  await panel.getByRole('button',{name:llmContinueName,exact:true}).click()
  const modelPrompt=panel.getByRole('textbox',{name:'实验提示词',exact:true})
  await modelPrompt.waitFor()
  assert.equal(await editor.count(),0)
  assert.equal(await check.count(),0)
  assert.equal(await complete.count(),0,'Reflection begins after the learner has observed a model answer')
  const modelRun=panel.getByRole('button',{name:'运行提示',exact:true})
  const originalPrompt=await modelPrompt.inputValue()
  await modelRun.click({trial:true})
  await modelRun.evaluate(button=>{button.click();button.click()})
  await panel.getByText('已收到回答',{exact:true}).waitFor()
  const response=panel.getByLabel('模型原始回答',{exact:true})
  assert.ok((await response.textContent()).includes('Observed model response'))
  let modelProjects=(await get('/projects')).projects
  const modelProject=modelProjects.find(project=>project.course_id==='llm-foundations')
  let modelHistory=(await get('/projects/'+modelProject.id+'/model-runs')).model_runs
  assert.equal(modelHistory.length,1,'Same-event clicks send one model request')
  assert.equal(modelHistory[0].prompt,originalPrompt)
  assert.equal(modelProject.progress.find(row=>row.lesson_id==='task-contract').state,'in_progress','Receiving a response cannot complete a lesson')
  let modelPosts=0
  for(const status of [503,409]){
    await modelPrompt.fill('只用材料回答。原文 / changed prompt '+status)
    await page.route('**/api/v1/academy/projects/*/model-runs',async route=>{
      if(route.request().method()!=='POST'){await route.continue();return}
      modelPosts++
      await route.fetch()
      await route.fulfill({status,contentType:'application/json',body:JSON.stringify({code:status===409?'academy/outcome-unknown':'fixture/lost-response',error:'Response unconfirmed after acceptance'})})
    })
    await modelRun.click()
    await panel.getByRole('button',{name:'回查操作回执',exact:true}).waitFor()
    await page.route('**/api/v1/academy/receipts/*',async route=>{
      const response=await route.fetch()
      const body=await response.json()
      body.result.model_run.prompt='Another input cannot confirm this request'
      await route.fulfill({response,json:body})
    })
    await panel.getByRole('button',{name:'回查操作回执',exact:true}).click()
    await panel.getByRole('alert').filter({hasText:invalidResponse}).waitFor()
    assert.equal(await panel.getByRole('button',{name:'回查操作回执',exact:true}).count(),1,'A mismatched model input cannot settle the pending request')
    await page.unroute('**/api/v1/academy/receipts/*')
    await panel.getByRole('button',{name:'回查操作回执',exact:true}).click()
    await page.waitForFunction(()=>!document.querySelector('#inspector-tab-content[data-panel="academy"] [role="alert"]'))
    await page.unroute('**/api/v1/academy/projects/*/model-runs')
    await panel.getByRole('combobox',{name:'实验历史',exact:true}).waitFor()
    await panel.getByText('已收到回答',{exact:true}).waitFor()
  }
  assert.equal(modelPosts,2,'Lost and domain-unknown model receipts are read back without repeating prompts')
  await panel.getByRole('radio',{name:'只用给定材料，并指出缺失信息。',exact:true}).check()
  await panel.getByRole('button',{name:'核对答案',exact:true}).click()
  await panel.getByRole('textbox',{name:'学习说明',exact:true}).fill('对照两次输入与回答：周一 09:00 有证据，周末开放时间未知。')
  await complete.click()
  await panel.getByText('本课学习活动已完成',{exact:true}).waitFor()
  modelHistory=(await get('/projects/'+modelProject.id+'/model-runs')).model_runs
  assert.equal(modelHistory.length,3)
  assert.equal(modelHistory[2].prompt,originalPrompt)
  await openAcademySection(panel, 'manage')
  await lessons.selectOption('context-selection')
  await panel.getByRole('button',{name:'保存当前版本并打开本课',exact:true}).click()
  await editor.waitFor()
  assert.ok((await editor.inputValue()).includes('def select_context'))
  await check.click()
  await panel.getByText('本课检查通过',{exact:true}).waitFor()
  await openAcademySection(panel, 'manage')
  await lessons.selectOption('evidence-assistant')
  await panel.getByRole('button',{name:'保存当前版本并打开本课',exact:true}).click()
  await editor.waitFor()
  await check.click()
  await panel.getByText('本课检查未通过',{exact:true}).waitFor()
  await disclose('资料与历史')
  await panel.getByRole('button',{name:'lessons/evidence-assistant/solution.py',exact:true}).click()
  const reference=panel.locator('pre').filter({hasText:'def answer_json'}).first()
  await reference.waitFor()
  const solution=await reference.textContent()
  assert.ok(solution.includes('if field in document:'))
  await editor.fill(solution)
  await openAcademySection(panel, 'code-options')
  await panel.getByRole('button',{name:'保存修改',exact:true}).click()
  await check.click()
  await panel.getByText('本课检查通过',{exact:true}).waitFor()
  projects=(await get('/projects')).projects
  assert.equal(projects.length,2)
  const llmProject=projects.find(project=>project.course_id==='llm-foundations')
  assert.equal(llmProject.progress.find(row=>row.lesson_id==='generation-evidence').state,'completed')
  assert.equal(llmProject.progress.find(row=>row.lesson_id==='evidence-assistant').state,'passed')
  assert.equal((await get('/projects/'+pythonProject.id)).project.progress.find(row=>row.lesson_id==='clean-text').state,'passed')
  assert.deepEqual((await get('/designs')).designs,[])
  for(const project of projects)assert.deepEqual((await get('/projects/'+project.id+'/submissions')).submissions,[],'Team course exercises need no model-backed submission')
  for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
    await page.request.patch(base+'/api/v1/ui-settings',{data:{language}})
    await page.setViewportSize({width,height:1000})
    await page.evaluate(theme=>localStorage.setItem('harness.ui-theme',theme),theme)
    await page.reload()
    await page.getByRole('textbox',{name:language==='en'?'Input message':'输入消息',exact:true}).waitFor()
    if(await page.locator('#task-inspector').getAttribute('aria-hidden')==='true')await page.keyboard.press('Control+j')
    await panel.getByRole('heading',{name:language==='en'?'Lesson 9 · Final task: a traceable answer helper':'第 9 课 · 结课任务：可追溯的回答助手',exact:true}).waitFor()
    const resources=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url),'utf-8'))
    const currentCourse=(await get('/courses/llm-foundations')).course
    const recommended=currentCourse.lessons.find(item=>!llmProject.progress.some(row=>row.lesson_id===item.id&&row.state!=='in_progress'))
    const actionName=resources['ui.academy.continueAction'].replace('{order}',recommended.order).replace('{title}',recommended.title)
    await panel.getByRole('button',{name:actionName,exact:true}).waitFor()
    assert.equal((await get('/projects/'+llmProject.id)).project.active_lesson_id,'evidence-assistant','Translated continuation controls cannot switch lessons automatically')
    await disclose(language==='en'?'Courses and projects':'课程与项目')
    assert.equal(await panel.locator('[data-course-id]').count(),3)
    await panel.locator('[data-course-id="python-foundations"]').getByRole('button',{name:language==='en'?'Continue course: Python: from first output to a small feature':'继续课程：Python：从第一行输出到独立小功能',exact:true}).waitFor()
    const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))
    assert.ok(bounds.scroll<=bounds.width,[language,theme,width].join('/'))
  }
  // 只控制已完成课程的目录投影；当前课仍使用真实检查证据，不改写后端进度。
  let finishedProjection=false,projectionWrites=0
  // 关闭前撤销拦截并等待已进入的回调，不能让 route.fetch 在浏览器关闭后收尾。
  releases.push(()=>page.unrouteAll({behavior:'wait'}))
  const completedCourse=(await get('/courses/llm-foundations')).course
  const originalProgress=(await get('/projects/'+llmProject.id)).project.progress
  await page.route('**/api/v1/academy/projects/'+llmProject.id,async route=>{
    if(!finishedProjection){await route.continue();return}
    const response=await route.fetch(),body=await response.json()
    const current=body.project.progress.find(row=>row.lesson_id===body.project.active_lesson_id)
    body.project.progress=completedCourse.lessons.map(lesson=>lesson.id===body.project.active_lesson_id?current:{
      lesson_id:lesson.id,state:lesson.mode==='python'?'passed':'completed',checkpoint_id:current.checkpoint_id,evidence_ids:['fixture-activity'],attempt_count:1,
    })
    await route.fulfill({response,json:body})
  })
  page.on('request',request=>{if(request.url().includes('/api/v1/academy/')&&request.method()!=='GET')projectionWrites++})
  const englishResources=JSON.parse(await readFile(new URL('../src/i18n/locales/en.json',import.meta.url),'utf-8'))
  await openAcademySection(panel, 'notes')
  const originalNote=await panel.getByRole('textbox',{name:englishResources['ui.academy.note'],exact:true}).inputValue()
  for(const finished of [false,true]){
    finishedProjection=finished
    await panel.getByRole('button',{name:'Refresh learning state',exact:true}).click()
    for(const language of ['en','zh-CN','en']){
      await page.keyboard.press('Control+,')
      await page.locator('#language-preference').selectOption(language)
      await page.waitForFunction(language=>document.documentElement.lang===language,language)
      await page.waitForFunction(()=>!document.getElementById('language-preference').disabled)
      await page.keyboard.press('Control+,')
      const resources=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url),'utf-8'))
      await panel.getByRole('heading',{name:resources['ui.academy.continueTitle'],exact:true}).waitFor()
      const continuation=panel.getByRole('button',{name:resources['ui.academy.continueAction'].split('{order}')[0]})
      if(finished){
        await panel.getByRole('status').filter({hasText:resources['ui.academy.activitiesComplete']}).waitFor()
        assert.equal(await continuation.count(),0)
      }else{
        await continuation.waitFor()
        await panel.getByText(resources['ui.academy.continueHint'],{exact:true}).waitFor()
      }
      assert.equal(await panel.getByRole('textbox',{name:resources['ui.academy.note'],exact:true}).inputValue(),originalNote)
      assert.equal(await panel.getByRole('textbox',{name:resources['ui.academy.source'],exact:true}).inputValue(),solution)
    }
  }
  assert.equal(projectionWrites,0,'Changing completion projections and language cannot execute learning actions')
  assert.deepEqual((await get('/projects/'+llmProject.id)).project.progress,originalProgress,'A completion projection cannot write real course progress')
  assert.deepEqual(errors,[])
  if(process.env.HARNESS_TEST_SCREENSHOTS){
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS,{recursive:true})
    await panel.locator('[data-course-id="python-foundations"]').scrollIntoViewIfNeeded()
    await page.screenshot({path:process.env.HARNESS_TEST_SCREENSHOTS+'/academy-team-courses-en-dark.png'})
  }
  console.log('Team curricula: one-click Python and LLM projects, guided code, failing transfer task, student repair, reading evidence, local experiments, reference code and language/theme/width matrix verified')
}catch(error){failure=error}
finally{
  clearTimeout(timer)
  await cleanupFixture({error:failure,browser,lines,backend,releases})
}
