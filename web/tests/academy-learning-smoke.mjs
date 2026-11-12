import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React, {useState} from 'react'
import {createRoot} from 'react-dom/client'
import {AcademyKnowledgeChecks} from '/src/components/inspector/AcademyKnowledgeChecks.tsx'
import {AcademyLearningPath} from '/src/components/inspector/AcademyLearningPath.tsx'
import {setLocale, useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.quizHelp=[]
function App(){
 const locale=useLocale(),zh=locale==='zh-CN'
 const [owner,setOwner]=useState('project-a'),[selected,setSelected]=useState('one')
 window.owner=setOwner
 const check={id:'constructor',prompt:zh?'模拟器是否会自动联网？':'Does this fixture connect automatically?',answer_id:'no',hints:zh?['看清实际动作。','区分文字和执行。']:['Inspect the actual action.','Separate text from execution.'],options:[
  {id:'yes',label:zh?'会自动联网':'Connects automatically',feedback:zh?'文字没有发起网络请求。':'Text did not issue a network request.'},
  {id:'no',label:zh?'只使用固定输入':'Uses fixed input',feedback:zh?'固定输入让本次观察可以核对。':'Fixed input makes this observation checkable.'}]}
 const lessons=['one','two','three'].map((id,index)=>({id,order:index+1,title:zh?'课次 '+(index+1):'Lesson topic '+(index+1),goal:'fixture',reading:'fixture',milestone:index===2,stage:index<2?(zh?'先理解':'Understand'):(zh?'再迁移':'Transfer'),run_command:['python','exercise.py'],source_manifest:[],references:[],prerequisites:[],trace_labels:{}}))
 const course={id:'course',title:'fixture',description:'fixture',version:'1',source_revision:'fixture',references:[],lessons}
 const project={id:owner,course_id:'course',active_lesson_id:'one',progress:[{lesson_id:'one',state:'in_progress'}]}
 return React.createElement('main',{'data-owner':owner,'data-lesson':selected,className:'mx-auto max-w-xl space-y-4 p-4'},
  React.createElement(AcademyLearningPath,{course,project,lessonId:selected,disabled:false,onSelect:setSelected}),
  React.createElement(AcademyKnowledgeChecks,{key:owner+selected,checks:[check],disabled:false,onAsk:(check,option)=>window.quizHelp.push([check,option])}))
}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
const html='<html><body><div id="root"></div><script type="module" src="/__academy-learning-entry.js"></script></body></html>'
await browserScenario(async({server,browser}) => {
  for (const theme of ['light','dark']) for (const width of [390,1440]) {
    const page=await browser.newPage({viewport:{width,height:1000}})
    const errors=[]
    page.on('pageerror',error=>errors.push(error.message))
    await page.goto(server.resolvedUrls.local[0]+'__academy-learning')
    await page.waitForFunction(()=>typeof window.locale==='function')
    await page.evaluate(theme=>document.documentElement.className=theme,theme)
    await page.evaluate(()=>window.locale('en'))
    await page.getByRole('radio',{name:'Connects automatically',exact:true}).check()
    const initialCheck=page.getByRole('button',{name:'Check my answer',exact:true})
    for (const keyCode of [13,229]) {
      const prevented=await initialCheck.evaluate((button,keyCode)=>!button.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode,isComposing:keyCode===13,bubbles:true,cancelable:true})),keyCode)
      assert.equal(prevented,true,'Composition Enter must be prevented')
    }
    await initialCheck.dispatchEvent('compositionstart')
    await initialCheck.press('Enter')
    assert.equal(await page.getByRole('status').count(),0,'Composition must not submit an answer')
    await initialCheck.dispatchEvent('compositionend')
    for (const language of ['en','zh-CN','en']) {
      const strings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))
      await page.evaluate(language=>window.locale(language),language)
      const check=page.getByRole('button',{name:strings['ui.academy.checkUnderstanding'],exact:true})
      const wrong=page.getByRole('radio',{name:language==='en'?'Connects automatically':'会自动联网',exact:true})
      const correct=page.getByRole('radio',{name:language==='en'?'Uses fixed input':'只使用固定输入',exact:true})
      if (language==='en' && !await wrong.isChecked()) {
        await wrong.focus();await wrong.press('Space')
        assert.equal(await wrong.isChecked(),true)
      }
      assert.equal(await wrong.isChecked(),true,'Language changes retain the answer and radio identity')
      await check.click()
      await page.getByText(strings['ui.academy.understandingRetry'],{exact:true}).waitFor()
      const help=page.getByRole('button',{name:strings['ui.academy.tutor.quizHelp'],exact:true})
      const count=await page.evaluate(()=>window.quizHelp.length)
      await help.dispatchEvent('compositionstart')
      await help.click()
      assert.equal(await page.evaluate(()=>window.quizHelp.length),count)
      await help.dispatchEvent('compositionend')
      await help.click()
      assert.deepEqual(await page.evaluate(()=>window.quizHelp.at(-1)),['constructor','yes'])
      assert.equal(await wrong.isChecked(),true)

      const hint=page.getByRole('button',{name:strings['ui.academy.showHint'].replace('{number}','1').replace('{total}','2'),exact:true})
      if (await hint.count()) await hint.click()
      await page.getByText(language==='en'?'Hint 1: Inspect the actual action.':'提示 1：看清实际动作。',{exact:true}).waitFor()
      if (language==='zh-CN') {
        await correct.check();await check.click()
        await page.getByText(strings['ui.academy.understandingCorrect'],{exact:true}).waitFor()
        assert.equal(await help.count(),0)
        await wrong.check();await check.click()
      }
      const path=page.locator('summary').filter({hasText:strings['ui.academy.learningPath'].replace('{count}','2')})
      if (await path.locator('..').getAttribute('open')===null) await path.click()
      assert.equal(await page.getByRole('list',{name:strings['ui.academy.pathStages'],exact:true}).locator(':scope > li').count(),2)
      assert.equal(await page.locator('[aria-current="step"]').count(),1)
      const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))
      assert.ok(bounds.scroll<=bounds.width,[theme,width,language].join('/'))
    }
    // 仅选课，不执行；换课或换项目不能继承旧回答与已展开的提示。
    await page.getByRole('button',{name:'Lesson 3 · Lesson topic 3',exact:true}).click()
    assert.equal(await page.getByRole('radio',{checked:true}).count(),0)
    assert.equal(await page.getByRole('status').count(),0)
    await page.getByRole('button',{name:'Continue lesson 1 · Lesson topic 1',exact:true}).click()
    await page.getByRole('radio',{name:'Uses fixed input',exact:true}).check()
    await page.evaluate(()=>window.owner('project-b'))
    await page.locator('main[data-owner="project-b"]').waitFor()
    assert.equal(await page.getByRole('radio',{checked:true}).count(),0)
    assert.deepEqual(errors,[])
    await page.close()
  }
  console.log('Academy learning: staged route, keyboard answers, targeted feedback, progressive hints, same-instance language, theme/width and project/lesson isolation verified')
},{entry:'/__academy-learning-entry.js',vite:{plugins:[{
  name:'academy-learning-fixture',resolveId(id){if(id==='/__academy-learning-entry.js')return id},load(id){if(id==='/__academy-learning-entry.js')return entry},
  configureServer(server){server.middlewares.use('/__academy-learning',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__academy-learning',html))}catch(error){next(error)}})},
}]}})
