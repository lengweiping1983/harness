import {requireAcademySandbox} from './academy-environment.mjs'
import {openAcademySection} from './academy-sections.mjs'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {fixturePython} from './python-fixture.mjs'
import {launchBrowser} from './browser-scenario.mjs'
import {cleanupFixture, withDeadline} from './fixture-cleanup.mjs'

const root=fileURLToPath(new URL('../../',import.meta.url))
const backend=spawn(fixturePython,['-m','tests.academy_web_fixture'],{cwd:root,stdio:['ignore','pipe','pipe']})
const lines=createInterface({input:backend.stdout})
let diagnostics='',browser,failure,timer
const releases=[]
backend.stderr.on('data',data=>{diagnostics+=data})
try {
  const ready=await new Promise((resolve,reject)=>{
    timer=setTimeout(()=>reject(Error('Academy topic fixture startup timed out: '+diagnostics)),20000)
    backend.once('error',reject)
    backend.once('exit',code=>reject(Error('Academy topic fixture exited: '+code+diagnostics)))
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
  await panel.locator('[data-academy-section="topic"] > summary').waitFor()
  await openAcademySection(panel, 'topic')
  const topic=panel.getByRole('textbox',{name:'你想学习什么？',exact:true})
  const generate=panel.getByRole('button',{name:'设计我的课程',exact:true})
  await topic.waitFor()
  assert.equal(await generate.isDisabled(),true)
  await openAcademySection(panel, 'catalog')
  const courseChoice=panel.getByRole('combobox',{name:'选择一门课程',exact:true})
  await courseChoice.locator('optgroup[label="团队课程"] option[value="harness-foundations"]').waitFor({state:'attached'})
  assert.deepEqual(await courseChoice.locator('optgroup[label="团队课程"] option').evaluateAll(options=>options.map(option=>option.value).sort()),['harness-foundations','llm-foundations','python-foundations'])
  const subject='清楚地解释一个设计思路'
  await topic.fill(subject)
  await panel.locator('summary').filter({hasText:'我已经会什么（可选）'}).click()
  await panel.getByRole('textbox',{name:'我已经会什么（可选）',exact:true}).fill('能写短句，希望用例子解释理由。')
  let designs=0,releasePost,postSeen,postHandled
  const gate=new Promise(resolve=>{releasePost=resolve}),observed=new Promise(resolve=>{postSeen=resolve}),handled=new Promise(resolve=>{postHandled=resolve})
  releases.push(releasePost)
  await page.route('**/api/v1/academy/designs',async route=>{
    if(route.request().method()!=='POST')return route.continue()
    designs++
    const response=await route.fetch()
    postSeen()
    await gate
    try{await route.fulfill({response})}catch(error){if(!route.request().failure())throw error}finally{postHandled()}
  })
  await topic.dispatchEvent('compositionstart')
  await topic.dispatchEvent('keydown',{key:'Enter',code:'Enter',keyCode:229,isComposing:true,bubbles:true})
  await generate.click()
  assert.equal(designs,0,'IME composition cannot start model-backed course design')
  await topic.dispatchEvent('compositionend')
  await generate.evaluate(button=>{button.click();button.click()})
  await withDeadline(()=>observed,15000,'course design admission')
  assert.equal(designs,1)
  assert.ok(await page.evaluate(()=>sessionStorage.getItem('harness.academy.design.pending.v1')))
  // 已受理写入在隐藏后继续；重新打开只按原请求号回查，旧回执不能重放或导航。
  await page.keyboard.press('Control+j')
  await page.waitForFunction(()=>document.querySelector('#task-inspector')?.getAttribute('aria-hidden')==='true')
  await page.keyboard.press('Control+j')
  await topic.waitFor()
  assert.equal(await topic.inputValue(),subject)
  const start=panel.getByRole('button',{name:'开始学习',exact:true})
  await start.waitFor()
  await page.waitForFunction(()=>sessionStorage.getItem('harness.academy.design.pending.v1')===null)
  releasePost();await handled;await page.unroute('**/api/v1/academy/designs')
  const records=(await get('/designs')).designs
  assert.equal(records.length,1)
  assert.equal(records[0].topic,subject)
  assert.equal(records[0].status,'ready')
  assert.equal(designs,1)
  assert.equal((await get('/projects')).projects.length,0,'Course generation cannot create a learning project or run code by itself')
  await courseChoice.locator('optgroup[label="我的课程"] option').waitFor({state:'attached'})
  await start.click()
  const complete=panel.locator('[data-academy-study-action]')
  await complete.waitFor()
  assert.equal(await complete.getAttribute('data-study-step'),'quiz')
  assert.equal(await panel.getByRole('textbox',{name:'练习代码',exact:true}).count(),0)
  assert.equal(await panel.getByRole('button',{name:'运行代码',exact:true}).count(),0)
  const note=panel.getByRole('textbox',{name:'学习说明',exact:true})
  await note.fill('我用一个具体例子说明设计，并解释了观察与结论的关系。')
  await panel.getByRole('radio',{name:'中文课程：guess',exact:true}).check()
  await panel.getByRole('button',{name:'核对答案',exact:true}).click()
  await panel.getByText('换个角度，再试一次。',{exact:true}).waitFor()
  assert.equal(await complete.getAttribute('data-study-step'),'quiz')
  await panel.getByRole('radio',{name:'中文课程：evidence',exact:true}).check()
  assert.equal(await complete.getAttribute('data-study-step'),'quiz','Selecting an answer alone is not a checked response')
  await panel.getByRole('button',{name:'核对答案',exact:true}).click()
  await page.waitForFunction(()=>[...document.querySelectorAll('#inspector-tab-content[data-panel="academy"] button')].some(button=>button.textContent==='完成本课学习活动'&&!button.disabled))
  let completions=0
  await page.route('**/api/v1/academy/projects/*/complete',async route=>{
    completions++
    await route.fetch()
    await route.abort('failed')
  })
  await note.dispatchEvent('compositionstart')
  await complete.click()
  assert.equal(completions,0)
  await note.dispatchEvent('compositionend')
  await complete.evaluate(button=>{button.click();button.click()})
  await panel.getByRole('button',{name:'回查操作回执',exact:true}).waitFor()
  await panel.getByRole('button',{name:'回查操作回执',exact:true}).click()
  await panel.getByText('本课学习活动已完成',{exact:true}).waitFor()
  assert.equal(completions,1)
  await page.unroute('**/api/v1/academy/projects/*/complete')
  let projects=(await get('/projects')).projects
  const studyProject=projects[0]
  assert.equal(studyProject.progress[0].state,'completed')
  assert.equal((await get('/projects/'+studyProject.id+'/runs')).runs.length,0)
  assert.equal((await get('/projects/'+studyProject.id+'/submissions')).submissions.length,0)
  await openAcademySection(panel, 'feedback')
  await panel.getByRole('button',{name:'提交并获取点评',exact:true}).click()
  await panel.getByRole('img',{name:/五维学习观察/}).waitFor()
  assert.equal((await get('/projects/'+studyProject.id)).project.progress[0].state,'completed','AI feedback must preserve study completion semantics')
  const lessons=panel.getByRole('combobox',{name:'课程目录',exact:true})
  await openAcademySection(panel, 'manage')
  await lessons.selectOption('practice')
  await panel.getByRole('button',{name:'保存当前版本并打开本课',exact:true}).click()
  await note.waitFor()
  await note.fill('第二课自己的独立例子与解释。')
  await panel.getByRole('radio',{name:'中文课程：evidence',exact:true}).check()
  await panel.getByRole('button',{name:'核对答案',exact:true}).click()
  await page.waitForFunction(()=>[...document.querySelectorAll('#inspector-tab-content[data-panel="academy"] button')].some(button=>button.textContent==='完成本课学习活动'&&!button.disabled))
  assert.equal(await complete.isDisabled(),false)
  await openAcademySection(panel, 'manage')
  await lessons.selectOption('notice')
  await panel.getByRole('button',{name:'保存当前版本并打开本课',exact:true}).waitFor()
  await openAcademySection(panel, 'manage')
  await lessons.selectOption('practice')
  await complete.waitFor()
  assert.equal(await complete.getAttribute('data-study-step'),'quiz','A→B→A must not revive previously checked answers')
  assert.equal(await panel.getByRole('radio',{checked:true}).count(),0)
  // 在已有项目中开始另一门编程课程，不把原项目身份套在创建回执上。
  await openAcademySection(panel, 'manage')
  await topic.fill('Python tiny functions')
  await generate.click()
  const pythonCard=panel.locator('[data-design-id]').filter({has:page.getByRole('heading',{name:'Python tiny functions',exact:true})})
  await pythonCard.getByRole('button',{name:'开始学习',exact:true}).click()
  const editor=panel.getByRole('textbox',{name:'练习代码',exact:true})
  await editor.waitFor()
  assert.ok((await editor.inputValue()).includes('def double(value)'))
  projects=(await get('/projects')).projects
  await requireAcademySandbox(projects.find(project=>project.id!==studyProject.id))
  await panel.getByRole('button',{name:'检查练习',exact:true}).click()
  await panel.getByText('本课检查通过',{exact:true}).waitFor()
  projects=(await get('/projects')).projects
  assert.equal(projects.length,2)
  assert.equal(projects.find(project=>project.id===studyProject.id).progress.find(row=>row.lesson_id==='notice').state,'completed')
  for(const language of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
    await page.request.patch(base+'/api/v1/ui-settings',{data:{language}})
    await page.setViewportSize({width,height:1000})
    await page.evaluate(theme=>localStorage.setItem('harness.ui-theme',theme),theme)
    await page.reload()
    await page.getByRole('textbox',{name:language==='en'?'Input message':'输入消息',exact:true}).waitFor()
    if(await page.locator('#task-inspector').getAttribute('aria-hidden')==='true')await page.keyboard.press('Control+j')
    await panel.getByRole('textbox',{name:language==='en'?'Practice code':'练习代码',exact:true}).waitFor()
    await panel.locator('summary').filter({hasText:language==='en'?'Courses and projects':'课程与项目'}).click()
    await openAcademySection(panel, 'topic')
    assert.equal(await panel.getByRole('textbox',{name:language==='en'?'What would you like to learn?':'你想学习什么？',exact:true}).inputValue(),'Python tiny functions')
    const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))
    assert.ok(bounds.scroll<=bounds.width,[language,theme,width].join('/'))
  }
  assert.deepEqual(errors,[])
  console.log('Academy topics: one-topic design, hidden accepted write recovery, two course groups, study practice/reflection/completion, lost receipt, ABA isolation, optional assessment and generated Python execution verified')
}catch(error){failure=error}
finally{
  clearTimeout(timer)
  await cleanupFixture({error:failure,browser,lines,backend,releases})
}
