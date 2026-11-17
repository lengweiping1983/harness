import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React, {useState} from 'react'
import {createRoot} from 'react-dom/client'
import {AcademyStudio} from '/src/components/inspector/AcademyStudio.tsx'
import {setLocale, useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.asked=[];window.opened=[];window.closedCount=0;window.completed=0
function App(){
 const locale=useLocale(),zh=locale==='zh-CN'
 const [revealed,setRevealed]=useState(false)
 const [answers,setAnswers]=useState(new Map())
 const [note,setNote]=useState('')
 const [completed,setCompleted]=useState(false)
 window.complete=()=>setCompleted(true)
 const check={id:'c1',prompt:zh?'哪一个是二分之一？':'Which is one half?',answer_id:'a',hints:[zh?'必须平均分。':'Parts must be equal.'],options:[
  {id:'a',label:zh?'两份相等中的一份':'One of two equal parts',feedback:zh?'相等的一份。':'Equal share.'},
  {id:'b',label:zh?'任意两块':'Any two pieces',feedback:zh?'不一定相等。':'Not necessarily equal.'}]}
 const step={id:'s1',kind:'numeric',prompt:zh?'分母是几？':'What is the denominator?',feedback:zh?'想想相等的份数。':'Think equal parts.',tolerance:0}
 const base={mode:'study',milestone:false,run_command:[],source_manifest:[],references:[],prerequisites:[],trace_labels:{},knowledge_checks:[check]}
 const lessons=[
  {...base,id:'place-value',order:1,title:zh?'数位':'Place value',goal:'fixture',reading:'fixture'},
  {...base,id:'fractions',order:2,title:zh?'分数':'Fractions',goal:zh?'理解二分之一':'Understand one half',reading:zh?'二分之一是两份相等中的一份。':'A half is one of two equal parts.',steps:[step],struggle:{challenge:zh?'不用数字表示一半。':'Show half without numbers.',window_minutes:3},world:{kind:'fraction-bar'}},
  {...base,id:'decimals',order:3,title:zh?'小数':'Decimals',goal:'fixture',reading:'fixture'}]
 const course={id:'math',title:zh?'数学基础':'Math foundations',description:'',version:'1',source_revision:'fixture',references:[],lessons}
 const project={id:'project',course_id:'math',course_version:'1',name:'Learner',revision:1,active_lesson_id:'fractions',course_fingerprint:'a'.repeat(64),created_at:1,progress:[{lesson_id:'place-value',state:'completed',checkpoint_id:'',evidence_ids:[],attempt_count:1}],root:'/fixture',fingerprint:'a'.repeat(64),sandbox:'unavailable'}
 const ready=Boolean(note.trim())&&answers.get('c1')==='a'
 return React.createElement(AcademyStudio,{course,project,lesson:lessons[1],active:false,disabled:false,tutorTurns:[],
  struggleRevealed:revealed,checkAnswers:answers,note,studyReady:ready,studyCompleted:completed,
  onClose:()=>{window.closedCount++},onOpenLesson:id=>{window.opened.push(id)},onAskCoach:prompt=>{window.asked.push(prompt)},
  onStrugglePhase:value=>{if(value)setRevealed(true)},
  onCheckAnswer:(id,answer)=>{setAnswers(previous=>{const next=new Map(previous);next.set(id,answer);return next})},
  onNote:value=>{setNote(value)},onComplete:()=>{window.completed++},onRefresh:()=>{}})
}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
const html='<html><body><div id="root"></div><script type="module" src="/__academy-studio-entry.js"></script></body></html>'
await browserScenario(async({server,browser}) => {
  for (const theme of ['light','dark']) for (const width of [390,1440]) {
    const page=await browser.newPage({viewport:{width,height:1000}})
    const errors=[]
    page.on('pageerror',error=>errors.push(error.message))
    await page.goto(server.resolvedUrls.local[0]+'__academy-studio')
    await page.waitForFunction(()=>typeof window.locale==='function')
    await page.evaluate(theme=>document.documentElement.className=theme,theme)
    await page.evaluate(()=>window.locale('en'))
    const strings=JSON.parse(await readFile(new URL('../src/i18n/locales/en.json',import.meta.url)))
    const dialog=page.getByRole('dialog',{name:strings['ui.academy.studio.dialogLabel'],exact:true})
    await dialog.waitFor()
    assert.equal(await page.locator('[data-studio-journey] button').count(),3,'Journey shows every lesson node')
    assert.equal(await page.locator('[data-studio-phase="struggle"]').count(),1,'Studio opens on the problem-first phase')

    // 教练自主性档位 + 输入法组合守卫：组合中的 Enter 不发送。
    await page.locator('[data-coach-level="2"]').click()
    const coachInput=page.getByRole('textbox',{name:strings['ui.academy.studio.coach.placeholder'],exact:true})
    await coachInput.fill('Why equal parts?')
    await coachInput.dispatchEvent('compositionstart')
    await coachInput.press('Enter')
    assert.equal(await page.evaluate(()=>window.asked.length),0,'Composition Enter must not ask the coach')
    await coachInput.dispatchEvent('compositionend')
    await coachInput.press('Enter')
    const explainPrompt=strings['ui.academy.studio.coach.prompt.explain'].replace('{title}','Fractions')
    assert.deepEqual(await page.evaluate(()=>window.asked.at(-1)),explainPrompt+'\nWhy equal parts?')
    await page.locator('[data-coach-level="0"]').click()
    await coachInput.press('Enter')
    assert.deepEqual(await page.evaluate(()=>window.asked.at(-1)),strings['ui.academy.studio.coach.prompt.try'].replace('{title}','Fractions'))

    // 单焦点环节轨道：步步闯关渲染步骤卡；快速测试答对后自动进入反思，反思后自动进入结课。
    await page.getByRole('button',{name:strings['ui.academy.studio.phase.steps'],exact:true}).click()
    await page.locator('[data-studio-phase="steps"] [data-academy-step="s1"]').waitFor()
    await page.getByRole('button',{name:strings['ui.academy.quickCheck'],exact:true}).click()
    await page.getByRole('radio',{name:'One of two equal parts',exact:true}).check()
    await page.getByRole('button',{name:strings['ui.academy.checkUnderstanding'],exact:true}).click()
    await page.locator('[data-studio-phase="note"]').waitFor()
    await page.getByRole('textbox',{name:strings['ui.academy.note'],exact:true}).fill('Half needs equal parts.')
    await page.locator('[data-studio-phase="finish"]').waitFor()
    await page.getByText(strings['ui.academy.studio.finish.checksDone'],{exact:true}).waitFor()
    await page.getByRole('button',{name:strings['ui.academy.completeStudy'],exact:true}).click()
    assert.equal(await page.evaluate(()=>window.completed),1)
    await page.evaluate(()=>window.complete())
    await page.getByText(strings['ui.academy.studio.finish.done'],{exact:true}).waitFor()

    // 同实例语言矩阵：en→zh-CN→en，状态保留，文案随语言解析。
    for (const language of ['en','zh-CN','en']) {
      const localeStrings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))
      await page.evaluate(language=>window.locale(language),language)
      await page.getByRole('dialog',{name:localeStrings['ui.academy.studio.dialogLabel'],exact:true}).waitFor()
      await page.getByRole('button',{name:localeStrings['ui.academy.studio.exit'],exact:true}).waitFor()
      await page.getByText(localeStrings['ui.academy.studio.finish.done'],{exact:true}).waitFor()
      await page.getByText(localeStrings['ui.academy.studio.coach.title'],{exact:true}).waitFor()
      assert.equal(await page.locator('[data-studio-journey] [aria-current="step"]').count(),1)
      const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))
      assert.ok(bounds.scroll<=bounds.width,[theme,width,language].join('/'))
    }

    // 旅程跳转与退出舞台。
    await page.getByRole('button',{name:'Place value',exact:true}).click()
    assert.deepEqual(await page.evaluate(()=>window.opened),['place-value'])
    await page.getByRole('button',{name:strings['ui.academy.studio.exit'],exact:true}).click()
    assert.equal(await page.evaluate(()=>window.closedCount),1)
    assert.deepEqual(errors,[])
    await page.close()
  }
  console.log('Academy studio: single-focus phases, autonomy coach with IME guard, auto-advance, journey navigation, language/theme/width matrix verified')
},{entry:'/__academy-studio-entry.js',vite:{plugins:[{
  name:'academy-studio-fixture',resolveId(id){if(id==='/__academy-studio-entry.js')return id},load(id){if(id==='/__academy-studio-entry.js')return entry},
  configureServer(server){server.middlewares.use('/__academy-studio',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__academy-studio',html))}catch(error){next(error)}})},
}]}})
