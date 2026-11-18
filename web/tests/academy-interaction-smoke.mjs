import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AcademyStepCard} from '/src/components/inspector/AcademySteps.tsx'
import {AcademyStudio} from '/src/components/inspector/AcademyStudio.tsx'
import {setLocale, useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale
const ordering={id:'ord',kind:'ordering',prompt:'Order the steps.',feedback:'Think order.',items:[{id:'a',label:'Alpha'},{id:'b',label:'Beta'},{id:'c',label:'Gamma'}]}
const matching={id:'mat',kind:'matching',prompt:'Pair them.',feedback:'Match.',left:[{id:'l1',label:'Left one'},{id:'l2',label:'Left two'}],right:[{id:'r1',label:'Right one'},{id:'r2',label:'Right two'}]}
const lesson={id:'l1',mode:'study',order:1,title:'Lesson',goal:'Goal',reading:'Reading',milestone:false,run_command:[],source_manifest:[],references:[],prerequisites:[],trace_labels:{},knowledge_checks:[]}
const course={id:'c1',version:'1',origin:'provided',source_revision:'f',title:'Course',description:'',references:[],lessons:[lesson]}
const project={id:'p1',course_id:'c1',course_version:'1',name:'Lab',revision:1,active_lesson_id:'l1',course_fingerprint:'a'.repeat(64),created_at:1,progress:[],root:'/x',fingerprint:'a'.repeat(64),sandbox:'unavailable'}
function App(){useLocale();return React.createElement('div',{className:'space-y-6 p-4'},
 React.createElement('div',{id:'order-card'},React.createElement(AcademyStepCard,{step:ordering,busy:false,feedback:null,onSubmit:()=>{},onNext:()=>{}})),
 React.createElement('div',{id:'match-card'},React.createElement(AcademyStepCard,{step:matching,busy:false,feedback:null,onSubmit:()=>{},onNext:()=>{}})),
 React.createElement('div',{id:'studio-done',style:{height:600,position:'relative',transform:'translateZ(0)'}},React.createElement(AcademyStudio,{course,project,lesson,active:false,disabled:false,tutorTurns:[],struggleRevealed:false,checkAnswers:new Map(),note:'done',studyReady:true,studyCompleted:true,onClose:()=>{},onOpenLesson:()=>{},onAskCoach:()=>{},onStrugglePhase:()=>{},onCheckAnswer:()=>{},onNote:()=>{},onComplete:()=>{},onRefresh:()=>{}})))}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
const html='<html><body><div id="root"></div><script type="module" src="/__academy-interaction-entry.js"></script></body></html>'
await browserScenario(async({server,browser}) => {
  for (const theme of ['light','dark']) for (const width of [390,1440]) {
    const page=await browser.newPage({viewport:{width,height:1000}})
    const errors=[]
    page.on('pageerror',error=>errors.push(error.message))
    await page.goto(server.resolvedUrls.local[0]+'__academy-interaction')
    await page.waitForFunction(()=>typeof window.locale==='function')
    await page.evaluate(theme=>document.documentElement.className=theme,theme)
    await page.evaluate(()=>window.locale('en'))
    // 拖拽排序：把第一张拖到第三张位置，顺序改变。
    const list=page.locator('#order-card [data-ordering-list]')
    const before=await list.locator('li span.min-w-0').allTextContents()
    await list.locator('[data-order-index="0"]').dragTo(list.locator('[data-order-index="2"]'))
    const after=await list.locator('li span.min-w-0').allTextContents()
    assert.notDeepEqual(after,before,'drag must reorder')
    assert.equal(after[2],'Alpha','dragged item lands at target')
    // 点选配对：先左后右，左槽显示右标签。
    const board=page.locator('#match-card [data-matching-board]')
    await board.locator('[data-match-left="l1"]').click()
    await board.locator('[data-match-right="r1"]').click()
    await board.locator('[data-match-left="l1"]').getByText('Right one',{exact:true}).waitFor()
    assert.equal(await board.locator('[data-match-right="r1"]').isDisabled(),true,'assigned right disabled')
    // 完成庆祝＋进度环。
    const studio=page.locator('#studio-done [data-academy-studio]')
    await studio.locator('[data-progress-ring]').waitFor()
    await studio.locator('[data-confetti]').waitFor()
    // 语言矩阵：交互文案随语言解析。
    for (const language of ['en','zh-CN','en']) {
      const copy=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url),'utf-8'))
      await page.evaluate(language=>window.locale(language),language)
      await page.locator('#match-card [data-match-left="l2"]').getByText(copy['ui.academy.steps.matchEmpty'],{exact:true}).waitFor()
      const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))
      assert.ok(bounds.scroll<=bounds.width,[theme,width,language].join('/'))
    }
    assert.deepEqual(errors,[])
    await page.close()
  }
  console.log('Academy interaction: drag reorder, click-to-pair matching, completion confetti, progress ring, language/theme/width matrix verified')
},{entry:'/__academy-interaction-entry.js',vite:{plugins:[{
  name:'academy-interaction-fixture',resolveId(id){if(id==='/__academy-interaction-entry.js')return id},load(id){if(id==='/__academy-interaction-entry.js')return entry},
  configureServer(server){server.middlewares.use('/__academy-interaction',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__academy-interaction',html))}catch(error){next(error)}})},
}]}})
