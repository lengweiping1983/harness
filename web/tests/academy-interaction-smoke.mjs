import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AcademyStepCard} from '/src/components/inspector/AcademySteps.tsx'
import {AcademyWorldCard} from '/src/components/inspector/AcademyWorld.tsx'
import {AcademyStudio} from '/src/components/inspector/AcademyStudio.tsx'
import {setLocale, useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.shaded=null;window.asked=[]
const ordering={id:'ord',kind:'ordering',prompt:'Order the steps.',feedback:'Think order.',items:[{id:'a',label:'Alpha'},{id:'b',label:'Beta'},{id:'c',label:'Gamma'}]}
const numeric={id:'num',kind:'numeric',prompt:'Pick on the line.',feedback:'Think.',tolerance:0,number_line:{min:0,max:12}}
const matching={id:'mat',kind:'matching',prompt:'Pair them.',feedback:'Match.',left:[{id:'l1',label:'Left one'},{id:'l2',label:'Left two'}],right:[{id:'r1',label:'Right one'},{id:'r2',label:'Right two'}]}
const lesson={id:'l1',mode:'study',order:1,title:'Lesson',goal:'Goal',reading:'Reading',milestone:false,run_command:[],source_manifest:[],references:[],prerequisites:[],trace_labels:{},knowledge_checks:[]}
const course={id:'c1',version:'1',origin:'provided',source_revision:'f',title:'Course',description:'',references:[],lessons:[lesson]}
const project={id:'p1',course_id:'c1',course_version:'1',name:'Lab',revision:1,active_lesson_id:'l1',course_fingerprint:'a'.repeat(64),created_at:1,progress:[],root:'/x',fingerprint:'a'.repeat(64),sandbox:'unavailable'}
const liveProps={course,project:{...project,id:'p2'},lesson,active:false,disabled:false,tutorTurns:[],struggleRevealed:false,checkAnswers:new Map(),note:'',studyReady:false,studyCompleted:false,onClose:()=>{},onOpenLesson:()=>{},onAskCoach:()=>{},onStrugglePhase:()=>{},onCheckAnswer:()=>{},onNote:()=>{},onComplete:()=>{},onRefresh:()=>{}}
function App(){useLocale();return React.createElement('div',{className:'space-y-6 p-4'},
 React.createElement('div',{id:'order-card'},React.createElement(AcademyStepCard,{step:ordering,busy:false,feedback:null,onSubmit:()=>{},onNext:()=>{}})),
 React.createElement('div',{id:'hint-card'},React.createElement(AcademyStepCard,{step:numeric,busy:false,feedback:{correct:false,text:'Not yet.',hints:['First hint','Second hint','Third hint']},onSubmit:()=>{},onNext:()=>{}})),
 React.createElement('div',{id:'num-card'},React.createElement(AcademyStepCard,{step:numeric,busy:false,feedback:null,onSubmit:()=>{},onNext:()=>{}})),
 React.createElement('div',{id:'match-card'},React.createElement(AcademyStepCard,{step:matching,busy:false,feedback:null,onSubmit:()=>{},onNext:()=>{}})),
 React.createElement('div',{id:'world-card'},React.createElement(AcademyWorldCard,{kind:'fraction-bar',session:{id:'w1',project_id:'p1',lesson_id:'l1',request_id:'r1',kind:'fraction-bar',params:{parts:4,shaded:1},challenge:{prompt:'Shade it.',options:[],target:{}},status:'observing',observations:[{params:{parts:4,shaded:1},state:{parts:4,shaded:1,label:'1/4'},at:1}],note:'',created_at:1,completed_at:null},params:{parts:4,shaded:1},note:'',busy:false,onParams:p=>{window.shaded=p.shaded},onNote:()=>{},onStart:()=>{},onPredict:()=>{},onObserve:()=>{},onComplete:()=>{}})),
 React.createElement('div',{id:'studio-live',style:{height:600,position:'relative',transform:'translateZ(0)'}},React.createElement(AcademyStudio,liveProps)),
 React.createElement('div',{id:'studio-done',style:{height:600,position:'relative',transform:'translateZ(0)'}},React.createElement(AcademyStudio,{course,project,lesson,active:false,disabled:false,tutorTurns:[],struggleRevealed:false,checkAnswers:new Map(),note:'done',studyReady:true,studyCompleted:true,onClose:()=>{},onOpenLesson:()=>{},onAskCoach:p=>{window.asked.push(p)},onStrugglePhase:()=>{},onCheckAnswer:()=>{},onNote:()=>{},onComplete:()=>{},onRefresh:()=>{}})))}
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
    // 完成庆祝＋进度环（挂载即检查，彩带短暂）。
    await page.locator('#studio-done [data-progress-ring]').waitFor()
    await page.locator('#studio-done [data-confetti]').waitFor()
    // 拖拽排序：把第一张拖到第三张位置，顺序改变。
    const list=page.locator('#order-card [data-ordering-list]')
    const before=await list.locator('li span.min-w-0').allTextContents()
    await list.locator('[data-order-index="0"]').dragTo(list.locator('[data-order-index="2"]'))
    const after=await list.locator('li span.min-w-0').allTextContents()
    assert.notDeepEqual(after,before,'drag must reorder')
    assert.equal(after[2],'Alpha','dragged item lands at target')
    await page.locator('#order-card [data-order-undo]').click()
    assert.deepEqual(await list.locator('li span.min-w-0').allTextContents(),before,'undo restores original order')
    await page.locator('#order-card [data-order-redo]').click()
    assert.equal((await list.locator('li span.min-w-0').allTextContents())[2],'Alpha','redo reapplies reorder')
    // 点选配对：先左后右，左槽显示右标签。
    const board=page.locator('#match-card [data-matching-board]')
    await board.locator('[data-match-left="l1"]').click()
    await board.locator('[data-match-right="r1"]').click()
    await board.locator('[data-match-left="l1"]').getByText('Right one',{exact:true}).waitFor()
    assert.equal(await board.locator('[data-match-right="r1"]').isDisabled(),true,'assigned right disabled')
    // 渐进式提示：初始只揭示第一条，点击揭示下一条。
    const hintBox=page.locator('#hint-card [data-progressive-hints]')
    await hintBox.getByText('First hint',{exact:true}).waitFor()
    assert.equal(await hintBox.getByText('Second hint',{exact:true}).count(),0,'second hint hidden initially')
    await page.locator('#hint-card [data-next-hint]').click()
    await hintBox.getByText('Second hint',{exact:true}).waitFor()
    // 可探索数轴：点/拖数轴点直接作答。
    const slider=page.locator('#num-card [role="slider"]')
    const box=await slider.boundingBox()
    await slider.click({position:{x:box.width*0.75,y:box.height/2}})
    assert.equal(await page.locator('#num-card input[type="number"]').inputValue(),'9','number line drag sets answer')
    // 可探索分数条：点/拖涂色格直接改 shaded。
    await page.locator('#world-card [data-shade-index="2"]').click()
    assert.equal(await page.evaluate(()=>window.shaded),3,'click shade sets shaded=3')
    // 键盘-first：方向键切换环节。
    await page.locator('#studio-done [data-studio-phases] button').last().click()
    await page.keyboard.press('ArrowLeft')
    await page.locator('#studio-done [data-studio-phase="note"]').waitFor()
    assert.equal(await page.evaluate(()=>document.activeElement?.tagName),'TEXTAREA','phase change auto-focuses primary input')
    // 会话连续性：live 舞台切到 note 后重载仍停留 note（按项目+课次持久化）。
    await page.locator('#studio-live [data-studio-phases] button').nth(1).click()
    await page.locator('#studio-live [data-studio-phase="note"]').waitFor()
    const phaseStorageKey='harness.academy.phase.v1:'+JSON.stringify(['p2','l1'])
    assert.equal(await page.evaluate(key=>localStorage.getItem(key),phaseStorageKey),'note','phase persisted per project+lesson')
    await page.reload()
    await page.waitForFunction(()=>typeof window.locale==='function')
    await page.locator('#studio-live [data-studio-phase="note"]').waitFor()
    assert.equal(await page.evaluate(key=>localStorage.getItem(key),phaseStorageKey),'note','saved phase survives reload')
    // 对话式快捷追问：点击 chip 以对应自主档发送教练请求。
    await page.locator('#studio-done [data-coach-suggest="hint"]').click()
    assert.equal(await page.evaluate(()=>window.asked.length),1,'suggestion chip asks coach')
    assert.ok((await page.evaluate(()=>window.asked[0])).includes('Lesson'),'prompt carries lesson context')
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
